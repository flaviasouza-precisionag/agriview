# Data Sources

## Agricultural field boundaries

This workflow requires agricultural field boundaries as a core input. These boundaries are used to identify road-field interfaces and generate field-facing Street View candidate points.

In the implementation that motivated this repository, field boundaries were provided by internal project resources and are not publicly distributed.

A public alternative for similar workflows is:

- Fields of The World: https://fieldsofthe.world/

This dataset provides agricultural field boundaries generated from satellite imagery and machine learning and may be used as input to this pipeline.

Expected input file:

```text
data/agricultural_field_boundaries.shp
```

Expected field ID column:

```text
Field_ID
```

## Road network

The road network used in this workflow was derived from OpenStreetMap (OSM).

OSM is suitable for agricultural Street View workflows because it includes detailed local and rural road features, such as:

- rural access roads;
- service roads;
- farm tracks;
- local roads near field boundaries.

These roads are important because valid Street View viewpoints for agricultural fields often occur along small local roads rather than major highways.

Expected input file:

```text
data/road_network_lines.shp
```

Expected road class column:

```text
fclass
```
