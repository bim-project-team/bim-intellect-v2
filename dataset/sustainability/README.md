# Carbon-factor data

`carbon_factors.csv` is intentionally a header-only template. BIM-Intellect does not ship invented or unsourced production factors.

Before embodied-carbon values can be calculated, an operator must add reviewed factor rows with a durable source, source version, region, year, dataset version, and compatible declared unit. `aliases` is a pipe-separated list of exact reviewed material aliases. Set `enabled=true` only for factors approved for use in the deployment.

Supported phase-one units are `kgCO2e/kg`, `kgCO2e/m3`, and `kgCO2e/m2`. The calculation engine rejects incompatible quantity dimensions. Values from different lifecycle boundaries should be kept in separate dataset versions/runs and their boundary documented in `notes`.

Example structure (the number below is deliberately omitted because the repository provides no authoritative source):

```text
factor_id,material_id,material_name,aliases,category,factor_value,factor_unit,region,source,source_version,year,dataset_version,notes,enabled
source-version-concrete,concrete,Concrete,Concrete - Cast in Situ|Cast-In-Place Concrete,concrete,<sourced value>,kgCO2e/m3,<region>,<publisher>,<version>,<year>,<dataset version>,<declared lifecycle boundary>,true
```
