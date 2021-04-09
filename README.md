cogserver
=========

Expose any GDAL recognized raster file as a HTTP accessible on-the-fly COG
(Cloud Optimized GeoTIFF)

Quality: *proof-of-concept*

What remains to be implemented:
- expose georeferencing through GeoTIFF tags
- expose metadata in GDAL_METADATA tag
- expose nodata
- allow compression
- expose overviews when they exist

License
-------

[Affero GPL v3](https://www.gnu.org/licenses/agpl-3.0.en.html)

Contact me if you need a different license than AGPL.

How to use
----------

Serve:
cogserver.py my_gdal_raster --port 8080

Consume:
gdalinfo /vsicurl/http://localhost:8080/my.tif
