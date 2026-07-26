# Input data

Production geospatial data are intentionally excluded from Git.

Expected field-centric inputs:

```text
data/
├── agricultural_field_boundaries.shp
└── road_network_lines.shp
```

The field layer should contain a unique identifier named `Field_ID` by default.
The road layer should contain a road-class attribute named `fclass` by default.

Shapefiles require their companion files (`.dbf`, `.shx`, `.prj`, and others).
GeoPackage inputs can be adopted by updating the corresponding configuration in the scripts.
