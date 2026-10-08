# Compatibility snapshot provenance

All responses originate from the audited **pre-UI** backend at commit
`74a968f517c656bd4e39dd99180c971913f376c4`.

- The root snapshots were captured on macOS/arm64 before workspace implementation.
- `linux-x86_64/` was captured using that commit's `app/` in the supported Linux/amd64
  container with the locked runtime dependencies. It was not generated from the new API.

PROJ produces small differences in the final decimal digits between these platforms.
The tests choose the corresponding baseline and compare exact serialized responses,
normalising UUIDs and timestamps only. Do not regenerate snapshots to make a changed API pass.
