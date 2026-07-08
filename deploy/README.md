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
```

The `ltp` account needs no sudo and should not get any.

## Install (as ltp)

```bash
sudo -iu ltp
git clone https://github.com/jhaigit/LED-topology.git ~/LED-topology
cd ~/LED-topology
python3 -m venv venv
venv/bin/pip install -e ".[controller,serial]"
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

## Enable the services (admin)

```bash
sudo cp deploy/ltp-controller.service deploy/ltp-serial-sink-fleet.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ltp-controller
sudo systemctl enable --now ltp-serial-sink-fleet   # serial hosts only
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
```

Then hard-refresh any open browser tabs.
