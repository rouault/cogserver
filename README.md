cogserver
=========

Expose any GDAL recognized raster file as a HTTP accessible on-the-fly COG
(Cloud Optimized GeoTIFF)

The on-the-fly COG file is not materialized to disk, can be of arbitrary size
with little RAM consumption and can be accessed in a piecewise way with HTTP
GET Range header.

Quality: *proof-of-concept*

What remains to be implemented:
- expose metadata in GDAL_METADATA tag
- allow compression
- expose overviews when they exist

License
-------

[Affero GPL v3](https://www.gnu.org/licenses/agpl-3.0.en.html)

Contact me if you need a different license than AGPL.

Pre-requisites
--------------

- Python 3
- GDAL native library
- GDAL Python bindings

How to use
----------

Serve:
cogserver.py my_gdal_raster --port 8080

Consume:
gdalinfo /vsicurl/http://localhost:8080/my.tif
