# Shared Modbus TCP RS485 gateway — ha-solarman-maks

## Why this branch exists

Two Home Assistant config entries with the same Modbus TCP host/port but
different Modbus Unit IDs are connected to the **same** physical half-duplex
RS485 bus. The original transport created an independent TCP socket and
asyncio.Lock per inverter, allowing overlapping requests and stalled reconnects.

The experiment in this branch changes **only plain Modbus TCP**:
- One TCP socket and one serialized queue per `(host, port)`, across config entries.
- Different Modbus Unit IDs are preserved per request.
- Exact Modbus TCP MBAP-framed reads, including fragmented TCP packets.
- Transaction ID, Unit ID and protocol/length validation.
- Automatic reconnect with one bounded retry of **read-only** FC01–FC04.
- Preserves the original Solarman 14-second Modbus response budget (5s base ×3 −1),
  rather than the incorrect 5-second timeout of the first experimental build.
- Warns only for an unrecovered request, with function code, Modbus ID, stage,
  elapsed time; retry recoveries are counted in diagnostics.
- **No automatic retransmission of writes** on uncertain TCP errors.
- Shared connection reference counting and reset on repeated device errors.
- Per-gateway counters shown in Home Assistant's integration diagnostics:
  `connected`, `clients`, `successful_requests`, `failed_attempts`,
  `tcp_connections`, `recovered_reads`, `last_response_ms`,
  `last_failure`, `last_unit_id`, `last_error`.

Solarman V5 (`tcp`) and serial RTU (`modbus_rtu`) use their existing
code paths. This is a testing branch, not yet field-verified on the inverter bus.

## User-supplied device profiles

Profiles are now installed in the **root** of inverter_definitions, as
requested, so the normal profile selector can list them directly:
- `custom_components/solarman/inverter_definitions/SUN_100K.yaml` —
  SUN-100K PCS / Deye profile (40 groups).
- `custom_components/solarman/inverter_definitions/gen_datacom.yaml` —
  DATAKOM D500 generator controller (6 groups).

Identical copies also remain in `inverter_definitions/custom/` so Home Assistant
config entries already set to `custom/SUN_100K.yaml` or
`custom/gen_datacom.yaml` continue working. Both locations have the same
contents in the release ZIP.

The source filename `SUN_100K (1).yaml` was normalized to `SUN_100K.yaml`.
To resolve a **real Home Assistant unique-ID collision** in the original SUN
profile, only the control label at register 2104 and its declared unique_id
were changed from `Grid Peak shaving` / `grid_peak_shaving` to
`PCS Grid Peak Shaving` / `pcs_grid_peak_shaving`. The second, distinct
`Grid Peak shaving` control at register 0x00BF was not changed. All register
maps, multipliers and units are unchanged. The Datakom profile is byte-identical
to the user upload.

## Obtain the test package

Open **Actions → Modbus TCP Gateway - Regression & Test Package**, open the
successful latest run of this branch, download artifact
`ha-solarman-maks-test-package`, and extract `solarman.zip`.

The ZIP contains the *contents* of `custom_components/solarman` (not its
parent directory). To test before merging, use manual installation:

1. On HAOS, use Samba to back up `/config/custom_components/solarman` and the Home Assistant
   configuration. Do not delete Solarman config entries or entities.
2. Unpack `solarman.zip` on your PC or Mac. It contains the contents of the integration folder; create a folder named `solarman` around these files.
3. Using your HAOS Samba `config` share, replace the old
   `custom_components/solarman/` directory with that extracted `solarman`
   folder. Keep the old copy as a backup. Confirm
   `pysolarman/shared_modbus_tcp.py` and root-level
   `inverter_definitions/SUN_100K.yaml`,
   `inverter_definitions/gen_datacom.yaml` exist.
4. Restart Home Assistant Core from Settings → System. Set both inverter config entries to
   the same ETH-485 gateway IP/port with `Modbus TCP` transport and
   **distinct** `Modbus Slave ID` values.
5. Observe stable updates for both inverters for a period longer than the
   previous failure interval. Download diagnostics for each integration entry.
6. Disable automatic HACS updates for Solarman while using this manually
   installed experiment; an upstream HACS update can overwrite the files.

No command-line access is needed. Home Assistant OS commonly exposes `config`
as a Samba share. If existing config entries reference the former root profile
file `SUN_100K (1).yaml`, select `SUN_100K.yaml` in their options.

## Diagnostics and fault isolation

Enable logging in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.solarman.pysolarman.shared_modbus_tcp: debug
    custom_components.solarman.device: debug
```

In the integration diagnostics, compare the shared `tcp_connections` and
`failed_attempts` counters across the two config entries. `clients: 2`
and the same counters on both devices confirm they share one pool entry.

During an outage, capture `tcpdump` on the HA host and note: TCP connection
count; whether the gateway returns MBAP replies; timeouts; Unit ID and
Transaction ID mismatches. Physical RS485 termination, gateway TCP server
multi-client options and bus overload remain independent possible causes.

Do not issue writes to inverter or generator settings merely to test recovery.

## Rollback

Stop HA Core, restore the saved `custom_components/solarman` directory,
start Core, and reload the two existing integrations. Device/entity
registrations stay intact because the integration domain is unchanged.

## Maintenance

Tests: `python -m unittest discover -s tests -v`.
Validation/test artifact: `.github/workflows/shared-modbus-gateway.yml`.
The upstream repository is `davidrapan/ha-solarman`, but changes here are
made against `maksbit/ha-solarman-maks` only.
