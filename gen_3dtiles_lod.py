#!/usr/bin/env python3
"""
gen_3dtiles_lod.py — 多级 LOD 白膜 3D Tiles 生成器

三级 LOD 策略:
  LOD 0 (远景): 建筑合并为矩形包围盒拉伸
  LOD 1 (中景): 建筑轮廓简化后拉伸
  LOD 2 (近景): 原始轮廓全精度拉伸

每个四叉树中间节点都生成粗糙 GLB (LOD 0/1)，
Cesium 在远处显示粗糙模型，拉近后 REPLACE 为精细模型。

Usage:
    uv run --no-project gen_3dtiles_lod.py
    uv run --no-project gen_3dtiles_lod.py --max-per-tile 800 --output my_output
"""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "geopandas>=0.14",
#   "pyogrio",
#   "numpy",
#   "pygltflib",
#   "shapely>=2.0",
# ]
# ///

import argparse
import json
import math
import os
import sys
import time
import warnings

# 强制 UTF-8 输出，避免 Windows GBK 控制台和 PyInstaller 打包环境下 emoji/中文编码错误
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

warnings.filterwarnings("ignore", category=UserWarning, message=".*Geometry is in a geographic CRS.*")

import geopandas as gpd
import numpy as np
import pygltflib
from pyproj import Transformer
from shapely.geometry import box as shapely_box
from shapely.ops import unary_union

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_INPUT         = "广州市建筑轮廓/广州市建筑轮廓.shp"
DEFAULT_OUTPUT        = "广州市白膜3dtiles-lod"
DEFAULT_HEIGHT_FIELD  = "Height"
DEFAULT_HEIGHT_VALUE  = 10.0
DEFAULT_MAX_PER_TILE  = 500
DEFAULT_MAX_DEPTH     = 12

# LOD 层级切换阈值 (四叉树深度)
LOD0_MAX_DEPTH = 3   # depth 0~3: 生成 LOD 0 (合并包围盒)
LOD1_MAX_DEPTH = 7   # depth 4~7: 生成 LOD 1 (简化轮廓)
                     # depth 8+:  叶子节点 LOD 2 (全精度)

SIMPLIFY_TOLERANCE = 0.0002  # LOD 1 简化容差 (~22m at equator)

# ─────────────────────────────────────────────────────────────────────────────
# Coordinate helpers
# ─────────────────────────────────────────────────────────────────────────────
_TO_ECEF = Transformer.from_crs("EPSG:4326", "EPSG:4978", always_xy=True)


def lonlat_to_ecef(lon: float, lat: float, alt: float = 0.0):
    x, y, z = _TO_ECEF.transform(lon, lat, alt)
    return float(x), float(y), float(z)


def tile_transform(lon0: float, lat0: float, elevation_offset: float = 0.0) -> list:
    """4×4 column-major glTF node matrix: ENU Y-up local → ECEF.

    Cesium (3D Tiles 1.1) applies an implicit glTF Y-up → Z-up rotation
    R_implicit(x,y,z) = (x, -z, y) to all glTF content before placing it
    in world space.  To cancel that rotation we pre-multiply the pure
    ENU→ECEF matrix M by R_implicit_inv(x,y,z) = (x, z, -y):

        Cesium applies:  R_implicit * (R_implicit_inv * M_ecef) * v = M_ecef * v  ✓

    Column layout after R_implicit_inv:
      col 0  R_implicit_inv(East)   = (E_x,  E_z, -E_y)
      col 1  R_implicit_inv(Up)     = (U_x,  U_z, -U_y)
      col 2  R_implicit_inv(-North) = (-N_x,-N_z,  N_y)
      col 3  R_implicit_inv(Origin) = (O_x,  O_z, -O_y)
    """
    ox, oy, oz = lonlat_to_ecef(lon0, lat0, alt=elevation_offset)

    # WGS84 ellipsoid surface normal ("up") via small altitude delta
    dx, dy, dz = lonlat_to_ecef(lon0, lat0, alt=elevation_offset + 1.0)
    up = np.array([dx - ox, dy - oy, dz - oz], dtype=np.float64)
    up /= np.linalg.norm(up)

    # East direction: perpendicular to up in the XY plane of ECEF
    lo = math.radians(lon0)
    east = np.array([-math.sin(lo), math.cos(lo), 0.0], dtype=np.float64)
    east /= np.linalg.norm(east)

    # North = cross(up, east) — completes the right-hand ENU frame
    north = np.cross(up, east)
    north /= np.linalg.norm(north)

    # Apply R_implicit_inv(x,y,z) = (x, z, -y) to each column's 3-D part
    return [
        float(east[0]),    float(east[2]),    float(-east[1]),   0.0,   # col 0
        float(up[0]),      float(up[2]),      float(-up[1]),     0.0,   # col 1
        float(-north[0]),  float(-north[2]),  float(north[1]),   0.0,   # col 2
        ox,    oz,    -oy,   1.0,                                         # col 3
    ]


def lonlat_to_enu(lons, lats, lon0: float, lat0: float):
    R = 6378137.0
    cos_lat0 = math.cos(math.radians(lat0))
    k = math.pi / 180.0 * R
    east  = (np.asarray(lons, dtype=np.float64) - lon0) * cos_lat0 * k
    north = (np.asarray(lats, dtype=np.float64) - lat0) * k
    return east, north


# ─────────────────────────────────────────────────────────────────────────────
# Geometry: full polygon extrusion (LOD 2)
# ─────────────────────────────────────────────────────────────────────────────

def _fan_triangulate(n: int) -> list:
    return [idx for i in range(1, n - 1) for idx in (0, i, i + 1)]


def polygon_to_mesh(poly, height: float, lon0: float, lat0: float):
    """Extrude polygon to 3D mesh (roof + walls, no bottom)."""
    coords = np.array(poly.exterior.coords[:-1])
    n = len(coords)
    if n < 3:
        return None, None

    east, north = lonlat_to_enu(coords[:, 0], coords[:, 1], lon0, lat0)

    all_verts = []
    all_faces = []
    offset = 0

    # Roof
    roof = np.column_stack([east,
                            np.full(n, height, dtype=np.float64),
                            -north]).astype(np.float32)
    all_verts.append(roof)
    raw = _fan_triangulate(n)
    for k in range(0, len(raw), 3):
        all_faces.append([offset + raw[k], offset + raw[k + 1], offset + raw[k + 2]])
    offset += n

    # Walls (no bottom face)
    for i in range(n):
        j = (i + 1) % n
        e0, n0 = float(east[i]), float(north[i])
        e1, n1 = float(east[j]), float(north[j])

        wall = np.array([
            [e0, 0.0,    -n0],
            [e1, 0.0,    -n1],
            [e1, height, -n1],
            [e0, height, -n0],
        ], dtype=np.float32)
        all_verts.append(wall)
        b = offset
        all_faces.append([b, b+2, b+1])
        all_faces.append([b, b+3, b+2])
        offset += 4

    verts = np.concatenate(all_verts, axis=0, dtype=np.float32)
    faces = np.array(all_faces, dtype=np.uint32)
    return verts, faces


# ─────────────────────────────────────────────────────────────────────────────
# LOD 0: merged bounding boxes
# ─────────────────────────────────────────────────────────────────────────────

def _box_to_mesh(minx, miny, maxx, maxy, height, lon0, lat0):
    """Extrude a rectangle to a box mesh (5 faces, no bottom)."""
    corners_lon = [minx, maxx, maxx, minx]
    corners_lat = [miny, miny, maxy, maxy]
    east, north = lonlat_to_enu(corners_lon, corners_lat, lon0, lat0)

    # 4 top vertices + walls
    verts = []
    faces = []
    offset = 0

    # Roof (4 vertices, 2 triangles)
    roof = np.array([
        [east[0], height, -north[0]],
        [east[1], height, -north[1]],
        [east[2], height, -north[2]],
        [east[3], height, -north[3]],
    ], dtype=np.float32)
    verts.append(roof)
    faces.append([0, 1, 2])
    faces.append([0, 2, 3])
    offset += 4

    # 4 walls
    for i in range(4):
        j = (i + 1) % 4
        e0, n0 = float(east[i]), float(north[i])
        e1, n1 = float(east[j]), float(north[j])
        wall = np.array([
            [e0, 0.0,    -n0],
            [e1, 0.0,    -n1],
            [e1, height, -n1],
            [e0, height, -n0],
        ], dtype=np.float32)
        verts.append(wall)
        b = offset
        faces.append([b, b+2, b+1])
        faces.append([b, b+3, b+2])
        offset += 4

    return np.concatenate(verts, axis=0, dtype=np.float32), np.array(faces, dtype=np.uint32)


def generate_lod0_mesh(gdf_sub, lon0, lat0, height_field, default_height, max_buildings=300):
    """
    LOD 0: 取面积最大的若干建筑生成简易包围盒，避免生成百MB级别的粗糙模型导致加载崩溃。
    """
    v_offset = 0
    all_verts = []
    all_faces = []

    areas = gdf_sub.geometry.area
    if len(gdf_sub) > max_buildings:
        top_indices = areas.nlargest(max_buildings).index
        gdf_subset = gdf_sub.loc[top_indices]
    else:
        gdf_subset = gdf_sub

    for _, row in gdf_subset.iterrows():
        geom = row.geometry
        if geom.is_empty:
            continue
        minx, miny, maxx, maxy = geom.bounds
        # 跳过太小的碎片
        if (maxx - minx) < 1e-6 or (maxy - miny) < 1e-6:
            continue

        h = row.get(height_field, None)
        h = float(h) if (h is not None and h == h) else default_height
        h = max(h, 3.0)

        v, f = _box_to_mesh(minx, miny, maxx, maxy, h, lon0, lat0)
        if v is not None:
            all_verts.append(v)
            all_faces.append(f + v_offset)
            v_offset += len(v)

    if not all_verts:
        return None, None

    return (np.concatenate(all_verts, axis=0, dtype=np.float32),
            np.concatenate(all_faces, axis=0, dtype=np.uint32))


# ─────────────────────────────────────────────────────────────────────────────
# LOD 1: simplified polygons
# ─────────────────────────────────────────────────────────────────────────────

def generate_lod1_mesh(gdf_sub, lon0, lat0, height_field, default_height,
                       tolerance=SIMPLIFY_TOLERANCE, max_buildings=1000):
    """
    LOD 1: 控制总数，简化建筑轮廓后拉伸。
    对较大的建筑的轮廓做 simplify(tolerance)，减少顶点数。
    """
    areas = gdf_sub.geometry.area
    if len(gdf_sub) > max_buildings:
        top_indices = areas.nlargest(max_buildings).index
        gdf_subset = gdf_sub.loc[top_indices]
    else:
        gdf_subset = gdf_sub

    all_verts = []
    all_faces = []
    v_offset = 0

    for _, row in gdf_subset.iterrows():
        geom = row.geometry
        h = row.get(height_field, None)
        h = float(h) if (h is not None and h == h) else default_height
        h = max(h, 1.0)

        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            simplified = poly.simplify(tolerance, preserve_topology=True)
            if simplified.is_empty or simplified.geom_type != "Polygon":
                continue
            try:
                v, f = polygon_to_mesh(simplified, h, lon0, lat0)
            except Exception:
                continue
            if v is None:
                continue
            all_verts.append(v)
            all_faces.append(f + v_offset)
            v_offset += len(v)

    if not all_verts:
        return None, None

    return (np.concatenate(all_verts, axis=0, dtype=np.float32),
            np.concatenate(all_faces, axis=0, dtype=np.uint32))


# ─────────────────────────────────────────────────────────────────────────────
# LOD 2: full detail (original)
# ─────────────────────────────────────────────────────────────────────────────

def generate_lod2_mesh(gdf_sub, lon0, lat0, height_field, default_height):
    """LOD 2: 原始轮廓全精度拉伸。"""
    all_verts = []
    all_faces = []
    v_offset = 0

    for _, row in gdf_sub.iterrows():
        geom = row.geometry
        h = row.get(height_field, None)
        h = float(h) if (h is not None and h == h) else default_height
        h = max(h, 1.0)

        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            try:
                v, f = polygon_to_mesh(poly, h, lon0, lat0)
            except Exception:
                continue
            if v is None:
                continue
            all_verts.append(v)
            all_faces.append(f + v_offset)
            v_offset += len(v)

    if not all_verts:
        return None, None

    return (np.concatenate(all_verts, axis=0, dtype=np.float32),
            np.concatenate(all_faces, axis=0, dtype=np.uint32))


# ─────────────────────────────────────────────────────────────────────────────
# GLB writer
# ─────────────────────────────────────────────────────────────────────────────

def write_glb(verts, faces, out_path: str, lon0: float, lat0: float, elevation_offset: float) -> bool:
    """Write vertices + faces to a GLB file.
    
    The ENU→ECEF transform is embedded as the glTF node matrix,
    so the tileset.json does NOT need a 'transform' on the tile node.
    This prevents hierarchical transform composition issues.
    """
    if verts is None or faces is None:
        return False

    faces_flat = faces.flatten().astype(np.uint32)
    vb = verts.tobytes()
    fb = faces_flat.tobytes()
    blob = vb + fb

    # Embed the ENU→ECEF transform as the glTF node matrix
    xform = tile_transform(lon0, lat0, elevation_offset)

    gltf = pygltflib.GLTF2(
        asset=pygltflib.Asset(version="2.0"),
        scene=0,
        scenes=[pygltflib.Scene(nodes=[0])],
        nodes=[pygltflib.Node(mesh=0, matrix=xform)],
        meshes=[pygltflib.Mesh(primitives=[pygltflib.Primitive(
            attributes=pygltflib.Attributes(POSITION=0),
            indices=1,
            material=0,
        )])],
        materials=[pygltflib.Material(
            pbrMetallicRoughness=pygltflib.PbrMetallicRoughness(
                baseColorFactor=[0.85, 0.85, 0.85, 1.0],
                metallicFactor=0.0,
                roughnessFactor=1.0,
            ),
            doubleSided=True,
        )],
        accessors=[
            pygltflib.Accessor(
                bufferView=0, byteOffset=0,
                componentType=pygltflib.FLOAT,
                count=len(verts), type=pygltflib.VEC3,
                min=verts.min(axis=0).tolist(),
                max=verts.max(axis=0).tolist(),
            ),
            pygltflib.Accessor(
                bufferView=1, byteOffset=0,
                componentType=pygltflib.UNSIGNED_INT,
                count=len(faces_flat), type=pygltflib.SCALAR,
            ),
        ],
        bufferViews=[
            pygltflib.BufferView(
                buffer=0, byteOffset=0, byteLength=len(vb),
                target=pygltflib.ARRAY_BUFFER,
            ),
            pygltflib.BufferView(
                buffer=0, byteOffset=len(vb), byteLength=len(fb),
                target=pygltflib.ELEMENT_ARRAY_BUFFER,
            ),
        ],
        buffers=[pygltflib.Buffer(byteLength=len(blob))],
    )
    gltf.set_binary_blob(blob)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    gltf.save_binary(out_path)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# geometricError calculation
# ─────────────────────────────────────────────────────────────────────────────

def compute_geometric_error(depth: int, tile_deg: float) -> float:
    """
    根据深度和瓦片大小计算 geometricError。
    深层 → 更小的 error → 需要更近才替换。
    适度降低了 error，避免加载时过度 refine 导致网络请求拥堵。
    """
    tile_m = tile_deg * 111320.0
    # 基础: 瓦片宽度的 2%
    base_error = tile_m * 0.02

    # 稍微对浅层提高一点 error
    if depth <= LOD0_MAX_DEPTH:
        return max(base_error, 500.0 / (depth + 1))
    elif depth <= LOD1_MAX_DEPTH:
        return max(base_error, 100.0 / (depth - LOD0_MAX_DEPTH + 1))
    else:
        return base_error


# ─────────────────────────────────────────────────────────────────────────────
# Recursive quadtree builder with LOD content at every level
# ─────────────────────────────────────────────────────────────────────────────

def generate_node(bounds, gdf_sub, depth: int,
                  height_field: str, default_height: float,
                  output_dir: str, counter: list,
                  max_per_tile: int, max_depth: int,
                  elevation_offset: float) -> dict | None:
    if len(gdf_sub) == 0:
        return None

    minx, miny, maxx, maxy = bounds
    lon0 = (minx + maxx) / 2.0
    lat0 = (miny + maxy) / 2.0

    h_vals = gdf_sub[height_field].dropna() if height_field in gdf_sub.columns else []
    max_h = float(np.max(h_vals)) + 5.0 if len(h_vals) > 0 else default_height + 5.0

    tile_deg = max(maxx - minx, maxy - miny)

    node = {
        "boundingVolume": {
            "region": [
                math.radians(minx), math.radians(miny),
                math.radians(maxx), math.radians(maxy),
                elevation_offset, max_h + elevation_offset,
            ]
        },
        "refine": "REPLACE",
    }

    if depth < max_depth and len(gdf_sub) > max_per_tile:
        # ── 内部节点: 生成粗糙 LOD 内容 + 递归子节点 ─────────────────────
        tile_idx = counter[0]
        counter[0] += 1

        if counter[0] % 200 == 0:
            print(f"  Generated {counter[0]} tiles (depth={depth})...", flush=True)

        # 根据深度选择 LOD 级别
        if depth <= LOD0_MAX_DEPTH:
            # LOD 0: 抽取最大的几个建筑的包围盒
            verts, faces = generate_lod0_mesh(
                gdf_sub, lon0, lat0, height_field, default_height)
        else:
            # LOD 1: 抽取较大的建筑并简化轮廓
            verts, faces = generate_lod1_mesh(
                gdf_sub, lon0, lat0, height_field, default_height)

        glb_rel = f"tiles/{tile_idx}.glb"
        glb_abs = os.path.join(output_dir, glb_rel)

        if write_glb(verts, faces, glb_abs, lon0, lat0, elevation_offset):
            node["content"] = {"uri": glb_rel}

        # 递归子树
        midx = (minx + maxx) / 2.0
        midy = (miny + maxy) / 2.0
        quadrants = [
            (minx, miny, midx, midy),
            (midx, miny, maxx, midy),
            (minx, midy, midx, maxy),
            (midx, midy, maxx, maxy),
        ]

        children = []
        sindex = gdf_sub.sindex
        for q in quadrants:
            cands = list(sindex.intersection(q))
            if not cands:
                continue
            sub = gdf_sub.iloc[cands]
            sub = sub[sub.intersects(shapely_box(*q))]
            if len(sub) == 0:
                continue
            child = generate_node(q, sub, depth + 1,
                                  height_field, default_height,
                                  output_dir, counter,
                                  max_per_tile, max_depth,
                                  elevation_offset)
            if child:
                children.append(child)

        if not children:
            # 没有子节点，当作叶子
            node["geometricError"] = 0.0
            return node

        node["geometricError"] = compute_geometric_error(depth, tile_deg)
        node["children"] = children

    else:
        # ── 叶子节点: LOD 2 全精度 ──────────────────────────────────────
        tile_idx = counter[0]
        counter[0] += 1

        if counter[0] % 200 == 0:
            print(f"  Generated {counter[0]} tiles (leaf, depth={depth})...", flush=True)

        verts, faces = generate_lod2_mesh(
            gdf_sub, lon0, lat0, height_field, default_height)

        glb_rel = f"tiles/{tile_idx}.glb"
        glb_abs = os.path.join(output_dir, glb_rel)

        if not write_glb(verts, faces, glb_abs, lon0, lat0, elevation_offset):
            return None

        node["content"] = {"uri": glb_rel}
        node["geometricError"] = 0.0

    return node


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def list_fields_mode():
    """--list-fields <shp_path>: 输出字段名 JSON 后退出，供 Electron 调用。"""
    idx = sys.argv.index('--list-fields')
    shp_path = sys.argv[idx + 1]
    gdf = gpd.read_file(shp_path, engine="pyogrio", rows=1)
    cols = [c for c in gdf.columns if c != 'geometry']
    print(json.dumps(cols, ensure_ascii=False))
    sys.exit(0)


def main():
    if '--list-fields' in sys.argv:
        list_fields_mode()

    parser = argparse.ArgumentParser(
        description="Generate 3D Tiles 1.1 with multi-LOD quadtree from a building shapefile")
    parser.add_argument("--input",           default=DEFAULT_INPUT)
    parser.add_argument("--output",          default=DEFAULT_OUTPUT)
    parser.add_argument("--height-field",    default=DEFAULT_HEIGHT_FIELD)
    parser.add_argument("--default-height",  type=float, default=DEFAULT_HEIGHT_VALUE)
    parser.add_argument("--max-per-tile",    type=int,   default=DEFAULT_MAX_PER_TILE,
                        help="Max buildings per leaf tile (default: 500)")
    parser.add_argument("--max-depth",       type=int,   default=DEFAULT_MAX_DEPTH,
                        help="Max quadtree depth (default: 12)")
    parser.add_argument("--elevation-offset",type=float, default=15.0,
                        help="Global altitude offset to avoid clipping into terrain (default: 15.0)")
    args = parser.parse_args()

    t0 = time.time()

    # ── load ──────────────────────────────────────────────────────────────────
    print(f"Reading {args.input} ...")
    gdf = gpd.read_file(args.input, engine="pyogrio")
    print(f"  {len(gdf):,} buildings  |  CRS: {gdf.crs}  ({time.time()-t0:.1f}s)")

    if str(gdf.crs).upper() != "EPSG:4326":
        print("  Reprojecting to WGS-84 ...")
        gdf = gdf.to_crs("EPSG:4326")

    if args.height_field not in gdf.columns:
        print(f"  WARNING: '{args.height_field}' not found, using {args.default_height}m")
        gdf[args.height_field] = args.default_height

    bounds = tuple(map(float, gdf.total_bounds))
    print(f"  Bounds: {bounds}")

    # ── generate ──────────────────────────────────────────────────────────────
    os.makedirs(os.path.join(args.output, "tiles"), exist_ok=True)

    print(f"\n🏗️  Building multi-LOD quadtree")
    print(f"  LOD 0 (depth 0-{LOD0_MAX_DEPTH}): merged bounding boxes")
    print(f"  LOD 1 (depth {LOD0_MAX_DEPTH+1}-{LOD1_MAX_DEPTH}): simplified polygons (tolerance={SIMPLIFY_TOLERANCE}°)")
    print(f"  LOD 2 (leaves): full-detail polygons")
    print(f"  max {args.max_per_tile} bldgs/tile, max depth {args.max_depth}")

    counter = [0]
    root = generate_node(
        bounds, gdf, depth=0,
        height_field=args.height_field,
        default_height=args.default_height,
        output_dir=args.output,
        counter=counter,
        max_per_tile=args.max_per_tile,
        max_depth=args.max_depth,
        elevation_offset=args.elevation_offset,
    )

    if root is None:
        print("ERROR: no tiles generated.")
        sys.exit(1)

    # ── write tileset.json ────────────────────────────────────────────────────
    tileset = {
        "asset": {"version": "1.1"},
        "geometricError": root.get("geometricError", 10000),
        "root": root,
    }
    out_json = os.path.join(args.output, "tileset.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(tileset, f, ensure_ascii=False, separators=(",", ":"))

    elapsed = time.time() - t0
    print(f"\n✅ Done in {elapsed:.1f}s")
    print(f"  Total tiles : {counter[0]:,}")
    print(f"  Output      : {out_json}")
    print(f"  LOD levels  : LOD0 (boxes) → LOD1 (simplified) → LOD2 (full)")


if __name__ == "__main__":
    main()
