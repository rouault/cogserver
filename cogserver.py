#!/usr/bin/env python
###############################################################################
# $Id$
#
# Purpose:  Expose a GDAL file as a HTTP accessible on-the-fly COG
# Author:   Even Rouault <even dot rouault at spatialys.com>
#
###############################################################################
# Copyright (c) 2021, Even Rouault <even dot rouault at spatialys.com>
#
# This program is free software: you can redistribute it and/or  modify
# it under the terms of the GNU Affero General Public License, version 3,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
###############################################################################

# If you need a different license than AGPL, please contact me

from osgeo import gdal

import argparse
from http.server import BaseHTTPRequestHandler
from socketserver import TCPServer, BaseRequestHandler
from typing import Tuple, Callable
import struct


TIFF_BYTE = 1        # 8-bit unsigned integer
TIFF_ASCII = 2       # 8-bit bytes w/ last byte null
TIFF_SHORT = 3       # 16-bit unsigned integer
TIFF_LONG = 4        # 32-bit unsigned integer
TIFF_RATIONAL = 5    # 64-bit unsigned fraction
TIFF_SBYTE = 6       # 8-bit signed integer
TIFF_UNDEFINED = 7   # 8-bit untyped data
TIFF_SSHORT = 8      # 16-bit signed integer
TIFF_SLONG = 9       # 32-bit signed integer
TIFF_SRATIONAL = 10  # 64-bit signed fraction
TIFF_FLOAT = 11      # 2-bit IEEE floating point
TIFF_DOUBLE = 12     # 64-bit IEEE floating point
TIFF_IFD = 13        # 32-bit unsigned integer (offset)
TIFF_LONG8 = 16      # BigTIFF 64-bit unsigned integer
TIFF_SLONG8 = 17     # BigTIFF 64-bit signed integer
TIFF_IFD8 = 18       # BigTIFF 64-bit unsigned integer (offset)

TIFFTAG_IMAGEWIDTH = 256    # image width in pixels
TIFFTAG_IMAGELENGTH = 257   # image height in pixels
TIFFTAG_BITSPERSAMPLE = 258  # bits per channel (sample)
TIFFTAG_COMPRESSION = 259   # data compression technique
COMPRESSION_NONE = 1        # dump mode
COMPRESSION_LZW = 5         # Lempel-Ziv  & Welch
COMPRESSION_JPEG = 7        # JPEG DCT compression
COMPRESSION_ADOBE_DEFLATE = 8  # Deflate compression
TIFFTAG_PHOTOMETRIC = 262   # photometric interpretation
PHOTOMETRIC_MINISBLACK = 1  # min value is white
PHOTOMETRIC_RGB = 2         # RGB color model
TIFFTAG_SAMPLESPERPIXEL = 277  # samples per pixel
TIFFTAG_PLANARCONFIG = 284  # storage organization
PLANARCONFIG_CONTIG = 1     # single image plane
PLANARCONFIG_SEPARATE = 2   # separate planes of data
TIFFTAG_TILEWIDTH = 322     # tile width in pixels
TIFFTAG_TILELENGTH = 323    # tile height in pixels
TIFFTAG_TILEOFFSETS = 324   # offsets to data tiles
TIFFTAG_TILEBYTECOUNTS = 325  # byte counts for tiles
TIFFTAG_SAMPLEFORMAT = 339   # data sample format
SAMPLEFORMAT_UINT = 1        # unsigned integer data
SAMPLEFORMAT_INT = 2         # signed integer data
SAMPLEFORMAT_IEEEFP = 3      # IEEE floating point data
SAMPLEFORMAT_COMPLEXINT = 5  # complex signed int
SAMPLEFORMAT_COMPLEXIEEEFP = 6  # complex ieee floating

gdal.UseExceptions()
gdal_3_3 = int(gdal.VersionInfo('VERSION_NUM')) >= 3030000


class Raster:
    def __init__(self, ds):
        self.ds = ds
        self.width = self.ds.RasterXSize
        self.height = self.ds.RasterYSize
        self.bitspersample = gdal.GetDataTypeSize(
            self.ds.GetRasterBand(1).DataType)
        self.num_bands = self.ds.RasterCount
        self.tile_width = 512
        self.tile_height = 512
        self.tile_x_count = (
            self.width + self.tile_width - 1) // self.tile_width
        self.tile_y_count = (
            self.height + self.tile_height - 1) // self.tile_height
        self.tile_count = self.tile_x_count * self.tile_y_count


class TIFFGenerator:
    def __init__(self, request):
        self.bigtiff = False
        self.long_formatter = '<I'
        self.long_size = 4
        self.num_tags = 12
        self._init()
        if not self.bigtiff and self.getfilesize() >= (1 << 32):
            self.bigtiff = True
            self._init()

    def _init(self):

        if self.bigtiff:
            self.TIFF_SIGNATURE = b'\x49\x49\x2B\x00' + b'\x08\x00\x00\x00'
            self.sig_size = len(self.TIFF_SIGNATURE)
            self.ifd_offset_formatter = '<Q'
            self.ifd_offset_size = 8
            self.num_tags_formatter = '<Q'
            self.num_tags_size = 8
            self.number_of_values_formatter = '<Q'
            self.tag_data_or_offset_formatter = '<Q'
            self.tagsize = 20
            self.tileoffsetype = TIFF_LONG8
            self.tileoffsetsize = 8
        else:
            self.TIFF_SIGNATURE = b'\x49\x49\x2A\x00'
            self.sig_size = len(self.TIFF_SIGNATURE)
            self.ifd_offset_formatter = self.long_formatter
            self.ifd_offset_size = self.long_size
            self.num_tags_formatter = '<H'
            self.num_tags_size = 2
            self.number_of_values_formatter = self.long_formatter
            self.tag_data_or_offset_formatter = self.long_formatter
            self.tagsize = 12
            self.tileoffsetype = TIFF_LONG
            self.tileoffsetsize = 4

    def _getheadersize_without_tag_data(self):
        return self.sig_size + self.ifd_offset_size + self.num_tags_size + \
            self.num_tags * self.tagsize + self.ifd_offset_size

    def getheadersize(self):
        sz = self._getheadersize_without_tag_data()
        if rast.num_bands > 1:
            sz += self.long_size * rast.num_bands
        return sz

    def write_tag(self, tagid, tagtype, num_occurences, tagvalueoroffset):
        r = struct.pack('<H', tagid)
        r += struct.pack('<H', tagtype)
        r += struct.pack(self.number_of_values_formatter, num_occurences)
        r += struct.pack(self.tag_data_or_offset_formatter, tagvalueoroffset)
        return r

    def generate_header(self):
        r = self.TIFF_SIGNATURE
        first_ifd_offset = self.sig_size + self.ifd_offset_size
        r += struct.pack(self.ifd_offset_formatter, first_ifd_offset)

        tag_data_offset = self._getheadersize_without_tag_data()

        r += struct.pack(self.num_tags_formatter, self.num_tags)

        r += self.write_tag(TIFFTAG_IMAGEWIDTH, TIFF_LONG, 1, rast.width)

        r += self.write_tag(TIFFTAG_IMAGELENGTH, TIFF_LONG, 1, rast.height)

        if rast.num_bands > 1:
            bitspersample_offset = tag_data_offset
            tag_data_offset += self.long_size * rast.num_bands
            bitspersample_value = bitspersample_offset
        else:
            bitspersample_value = rast.bitspersample
        r += self.write_tag(TIFFTAG_BITSPERSAMPLE, TIFF_LONG,
                            rast.num_bands, bitspersample_value)

        r += self.write_tag(TIFFTAG_COMPRESSION,
                            TIFF_LONG, 1, COMPRESSION_NONE)

        r += self.write_tag(TIFFTAG_PHOTOMETRIC, TIFF_LONG, 1,
                            PHOTOMETRIC_MINISBLACK if rast.num_bands != 3 else PHOTOMETRIC_RGB)

        r += self.write_tag(TIFFTAG_SAMPLESPERPIXEL,
                            TIFF_LONG, 1, rast.num_bands)

        r += self.write_tag(TIFFTAG_PLANARCONFIG,
                            TIFF_LONG, 1, PLANARCONFIG_CONTIG)

        r += self.write_tag(TIFFTAG_TILEWIDTH, TIFF_LONG, 1, rast.tile_width)

        r += self.write_tag(TIFFTAG_TILELENGTH, TIFF_LONG, 1, rast.tile_height)

        if rast.tile_count == 1:
            data_offset = self.dataoffset()
            tileoffsets_offset = data_offset
            tilebytecounts_offset = self.tilesize()
        else:
            tileoffsets_offset = tag_data_offset
            tag_data_offset += rast.tile_count * self.tileoffsetsize
            tilebytecounts_offset = tag_data_offset
            tag_data_offset += rast.tile_count * self.tileoffsetsize

        r += self.write_tag(TIFFTAG_TILEOFFSETS, self.tileoffsetype,
                            rast.tile_count, tileoffsets_offset)

        r += self.write_tag(TIFFTAG_TILEBYTECOUNTS, self.tileoffsetype,
                            rast.tile_count, tilebytecounts_offset)

        r += self.write_tag(TIFFTAG_SAMPLEFORMAT,
                            TIFF_LONG, 1, SAMPLEFORMAT_UINT)

        next_ifd_offset = 0
        r += struct.pack(self.ifd_offset_formatter, next_ifd_offset)

        if rast.num_bands > 1:
            for i in range(rast.num_bands):
                r += struct.pack(self.long_formatter, rast.bitspersample)

        return r

    def tilesize(self):
        return rast.num_bands * rast.tile_width * rast.tile_height * rast.bitspersample // 8

    def generate_tileoffsets(self):
        data_offset = self.dataoffset()
        tile_size = self.tilesize()
        return b''.join([struct.pack(self.tag_data_or_offset_formatter, data_offset + tile_size * i) for i in range(rast.tile_count)])

    def generate_tilebytecounts(self):
        tile_size = self.tilesize()
        return b''.join([struct.pack(self.tag_data_or_offset_formatter, tile_size) for i in range(rast.tile_count)])

    def dataoffset(self):
        data_offset = self.getheadersize()
        if rast.tile_count > 1:
            data_offset += 2 * rast.tile_count * self.tileoffsetsize
        return data_offset

    def getfilesize(self):
        return self.dataoffset() + rast.tile_count * self.tilesize()

    def gettiledata(self, tile_num):
        #print('Reading tile %d' % tile_num)
        tile_y = tile_num // rast.tile_x_count
        tile_x = tile_num - tile_y * rast.tile_x_count
        xoff = tile_x * rast.tile_width
        yoff = tile_y * rast.tile_height
        xsize = rast.tile_width
        ysize = rast.tile_height
        if xoff + xsize > rast.width:
            xsize = rast.width - xoff
        if yoff + ysize > rast.height:
            ysize = rast.height - yoff

        dt = rast.ds.GetRasterBand(1).DataType
        dtsize = gdal.GetDataTypeSize(dt) // 8
        buf_pixel_space = dtsize * rast.num_bands
        buf_line_space = buf_pixel_space*rast.tile_width
        buf_band_space = dtsize if rast.num_bands > 1 else None

        if xsize < rast.tile_width or ysize < rast.tile_height:
            if gdal_3_3:
                buf_obj = bytearray(b'\x00' * self.tilesize())
                buf_obj = rast.ds.ReadRaster(xoff, yoff, xsize, ysize,
                                             buf_obj=buf_obj,
                                             buf_xsize=xsize,
                                             buf_ysize=ysize,
                                             buf_pixel_space=buf_pixel_space,
                                             buf_line_space=buf_line_space,
                                             buf_band_space=buf_band_space)
            else:
                buf_obj = rast.ds.ReadRaster(xoff, yoff, xsize, ysize)
                tmp_ds = gdal.GetDriverByName('MEM').Create(
                    '', rast.tile_width, rast.tile_height, rast.num_bands, dt)
                tmp_ds.WriteRaster(0, 0, xsize, ysize, buf_obj)
                buf_obj = tmp_ds.ReadRaster(0, 0, rast.tile_width, rast.tile_height,
                                            buf_pixel_space=buf_pixel_space,
                                            buf_line_space=buf_line_space,
                                            buf_band_space=buf_band_space)
        else:
            buf_obj = rast.ds.ReadRaster(xoff, yoff, xsize, ysize,
                                         buf_pixel_space=buf_pixel_space,
                                         buf_line_space=buf_line_space,
                                         buf_band_space=buf_band_space)
        return buf_obj


def generate_tiff(request):

    ctx = TIFFGenerator(request)
    filesize = ctx.getfilesize()

    if 'Range' in request.headers:
        rang = request.headers['Range']
        assert rang.startswith('bytes=')
        rang = rang[len('bytes='):]
        rang = rang.split('-')
        start = int(rang[0])
        assert start >= 0
        size = int(rang[1]) - start + 1
        assert size > 0

        if start >= filesize:
            request.send_header('Content-Length', 0)
            request.end_headers()
            return
        if start + size >= filesize:
            size = filesize - start

        request.send_response(206)
        request.send_header('Content-Length', size)
        request.send_header('Content-Type', 'image/geo+tiff')
        request.send_header('Content-Range', 'bytes %d-%d/%d' %
                            (start, start + size - 1, filesize))
        request.end_headers()

        non_data_length = ctx.dataoffset()
        if start < non_data_length:
            non_data = ctx.generate_header()
            if rast.tile_count > 1:
                non_data += ctx.generate_tileoffsets() + ctx.generate_tilebytecounts()
            non_data_extract = non_data[start:min(start+size, len(non_data))]
            request.wfile.write(non_data_extract)
            if start + size <= non_data_length:
                return
            start = non_data_length
            size -= len(non_data_extract)

        first_tile = (start - non_data_length) // ctx.tilesize()
        last_tile = (start + size - 1 - non_data_length) // ctx.tilesize()
        for tile_num in range(first_tile, last_tile+1):
            tiledata = ctx.gettiledata(tile_num)
            if tile_num == first_tile:
                off = start - (non_data_length + first_tile * ctx.tilesize())
                if tile_num == last_tile:
                    tiledata = tiledata[off:off+size]
                else:
                    tiledata = tiledata[off:]
            elif tile_num == last_tile:
                tiledata = tiledata[0:start+size -
                                    (non_data_length + last_tile * ctx.tilesize())]
            request.wfile.write(tiledata)
        return

    request.send_response(200)
    non_data = ctx.generate_header()
    if rast.tile_count > 1:
        non_data += ctx.generate_tileoffsets() + ctx.generate_tilebytecounts()
    request.send_header('Content-Length', filesize)
    request.send_header('Content-Type', 'image/geo+tiff')
    request.end_headers()
    request.wfile.write(non_data)
    for tile_num in range(rast.tile_count):
        request.wfile.write(ctx.gettiledata(tile_num))


class MyHandler(BaseHTTPRequestHandler):

    def do_HEAD(self):
        self.protocol_version = 'HTTP/1.1'
        if self.path.endswith('.tif'):
            self.send_response(200)
            self.send_header('Accept-Ranges', 'bytes')
            ctx = TIFFGenerator(self)
            self.send_header('Content-Length', ctx.getfilesize())
            self.send_header('Content-type', 'image/geo+tiff')
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        self.protocol_version = 'HTTP/1.1'
        if self.path.endswith('.tif'):
            generate_tiff(self)
        else:
            self.send_response(404)
            self.end_headers()


class MySockServer(TCPServer):
    def __init__(self, server_address: Tuple[str, int], RequestHandlerClass: Callable[..., BaseRequestHandler]):
        self.allow_reuse_address = True
        super().__init__(server_address, RequestHandlerClass)


def get_args():
    parser = argparse.ArgumentParser(
        description='Export a GDAL raster as a cloud-optimized file')
    parser.add_argument('filename',
                        help='Raster file')
    parser.add_argument('--port', type=int, default=8080,
                        help='TCP port')

    return parser.parse_args()


args = get_args()
filename = args.filename
port = args.port

if filename == '{dummy}':
    ds = gdal.GetDriverByName('MEM').Create('', 3000, 2000, 3, gdal.GDT_Byte)
    ds.GetRasterBand(1).Fill(255)
    ds.GetRasterBand(2).Fill(255)
else:
    ds = gdal.Open(filename)
rast = Raster(ds)

with MySockServer(("", port), MyHandler) as httpd:
    print("Serving %s at port %s" % (filename, port))
    httpd.serve_forever()
