# Deployment

systemd units for running LED Topology services under an unprivileged
`ltp` account. Paths assume the repo is checked out at
`/home/ltp/LED-topology` with a venv at `venv/` — adjust `ExecStart` and
config paths if yours differ.

## One-time host setup (admin)

```bash
sudo apt install -y python3-venv git
sudo useradd -m -s /usr/sbin/nologin ltp     # if the account doesn't exist
sudo usermod -aG dialout ltp                  # serial-sink host only
sudo usermod -aG i2c ltp                       # thermal-source host only
# thermal host also needs I2C enabled: raspi-config -> Interface Options -> I2C
```

The `ltp` account needs no sudo and should not get any.

## Install (as ltp)

```bash
sudo -iu ltp
git clone https://github.com/jhaigit/LED-topology.git ~/LED-topology
cd ~/LED-topology
python3 -m venv venv
venv/bin/pip install -e ".[controller,serial]"
# on a thermal-sensor host, include the thermal extra:
#   venv/bin/pip install -e ".[thermal]"
```

## Controller configuration

Generate credentials and write `~/controller.yaml` (see
`configs/controller-example.yaml` for all options):

```bash
venv/bin/ltp-controller --hash-password     # hash for web.auth users
venv/bin/ltp-controller --generate-token    # bearer token for scripts/HA
chmod 600 ~/controller.yaml
```

A non-loopback `web.host` requires the auth block (HTTPS then enables
itself, generating a self-signed cert into `~/.config/ltp/` on first
run) — or an explicit `allow_insecure_http: true` to accept cleartext.
The controller refuses to start otherwise. Verify interactively once:

```bash
venv/bin/ltp-controller -c ~/controller.yaml
# expect: Web interface available at https://...
```

## Serial sink fleet configuration

Write `~/serial-fleet.yaml` (see `configs/serial-fleet-example.yaml`).
Fleet mode probes matching USB serial ports and runs one sink per LTP
device found; names come from the devices themselves.

## Thermal source configuration

No config file — options are CLI flags on `ExecStart` in
`ltp-thermal-source.service` (`--name`, `--rate`, `--palette`, `--bus`,
`--address 0x69`; add `--sink host:port` to push to a fixed sink instead of
advertising for the controller to route). Verify the sensor is visible first:

```bash
i2cdetect -y 1        # expect 0x69 (or 0x68 with AD_SELECT low)
venv/bin/ltp-thermal-source            # Ctrl-C after it advertises
```

## Enable the services (admin)

```bash
sudo cp deploy/ltp-controller.service deploy/ltp-serial-sink-fleet.service \
        deploy/ltp-thermal-source.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ltp-controller
sudo systemctl enable --now ltp-serial-sink-fleet   # serial hosts only
sudo systemctl enable --now ltp-thermal-source       # thermal-sensor hosts only
journalctl -u ltp-controller -f
```

## Firewall

Inbound: `8080/tcp` (web UI/API). mDNS discovery needs `5353/udp`
multicast. Sink hosts additionally use dynamically-assigned TCP/UDP
ports for control/data — on a firewalled sink host, pin them in the
sink config instead of using auto ports.

## Upgrades

```bash
sudo -iu ltp bash -c 'cd ~/LED-topology && git pull && venv/bin/pip install -qe ".[controller,serial]"'
sudo systemctl restart ltp-controller ltp-serial-sink-fleet
# thermal-sensor host: add the thermal extra + restart its unit
#   sudo -iu ltp bash -c 'cd ~/LED-topology && git pull && venv/bin/pip install -qe ".[thermal]"'
#   sudo systemctl restart ltp-thermal-source
```

Then hard-refresh any open browser tabs.
