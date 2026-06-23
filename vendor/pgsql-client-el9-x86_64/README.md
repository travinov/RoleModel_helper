# PostgreSQL Client Bundle for EL9 x86_64

This directory contains user-space PostgreSQL client tools for RoleModel Helper upload backups on servers where `pg_dump` cannot be installed system-wide.

Source RPMs:
- https://mirror.stream.centos.org/9-stream/AppStream/x86_64/os/Packages/postgresql-15.12-1.module_el9+1199+0a6782cc.x86_64.rpm
- https://mirror.stream.centos.org/9-stream/AppStream/x86_64/os/Packages/postgresql-private-libs-15.12-1.module_el9+1199+0a6782cc.x86_64.rpm
- https://mirror.stream.centos.org/9-stream/AppStream/x86_64/os/Packages/libpq-13.23-1.el9.x86_64.rpm

Included files:
- `bin/pg_dump` (PostgreSQL 15.12)
- `bin/pg_restore` (PostgreSQL 15.12)
- `bin/psql` (PostgreSQL 15.12)
- `lib/libpq.so.5.13` and `lib/libpq.so.5`
- `lib/libpq.so.private15-5.15` and `lib/libpq.so.private15-5`

The application discovers `bin/pg_dump` automatically. When this bundle is used, `app.api.server` prepends this bundle's `lib/` directory to `LD_LIBRARY_PATH` only for the `pg_dump` subprocess.
