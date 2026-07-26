# Architecture

AgriView separates geospatial candidate generation, Street View metadata validation, and imagery
retrieval into independent stages.

## Field-centric candidate generation

1. Read agricultural field polygons and road lines.
2. Project both layers into a metric coordinate reference system.
3. identify road segments close enough to field boundaries.
4. derive valid road–field frontage geometries.
5. trim unstable frontage ends and reject poor geometric candidates.
6. calculate road-located points and field-facing camera headings.
7. export WGS84 coordinates for API use.

## Metadata-first design

AgriView queries the Street View Metadata API before requesting imagery. This supports availability
checks, date filtering, panorama tracking, cost control, and auditable acquisition logs.

## Reproducibility

Configuration is separated from credentials, generated outputs are excluded from Git, sample inputs
are provided, and each workflow is organized as explicit sequential stages.
