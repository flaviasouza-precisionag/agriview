# Methodology

This repository implements a field-centric workflow for identifying Google Street View imagery relevant to agricultural fields.

## 1. Field-road relationship

The workflow starts from two spatial inputs:

- agricultural field boundaries;
- road-network lines.

Roads are spatially compared with field boundaries using a distance threshold. Road segments close enough to a field are considered candidate road-field frontages.

## 2. Frontage extraction

For each field, the road geometry is clipped by a buffer around the field. This creates a road frontage segment, representing the portion of the road that is spatially associated with the field.

## 3. Edge trimming

The ends of each frontage are trimmed to reduce unstable locations such as:

- road curves;
- intersections;
- corners;
- blind positions near field edges.

## 4. Curve filtering

Candidate points located on strong local curves are removed. This is done by comparing the road direction before and after each candidate point.

If the angular change is too large, the point is discarded.

## 5. Candidate point generation

Two point-generation strategies are included:

### Adaptive sampling

The number of points depends on frontage length. Longer frontages receive more points, up to a defined maximum.

### One point per field

The algorithm selects the best frontage for each field and places one representative point at the center of that frontage.

## 6. Camera heading calculation

The Street View request point remains on the road. The field geometry is used to calculate the camera direction so the image is oriented toward the agricultural field.

The workflow generates:

- `heading_0`: central field-facing heading;
- `heading_m20`: central heading minus 20 degrees;
- `heading_p20`: central heading plus 20 degrees.

## 7. Metadata collection

The metadata step checks whether Street View imagery is available for each point and heading.

It returns:

- Street View status;
- panorama ID;
- image date;
- returned panorama coordinates.

## 8. Image download

The final step downloads images from valid metadata records and stores a download log for reproducibility.
