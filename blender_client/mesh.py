from ..broadcaster import common

import logging
import struct

import bpy
import bmesh
from mathutils import Vector

logger = logging.getLogger(f"dccsync")


def deprecated_buildMesh(client, data):
    # Deprecated: Blender does not load a baked mesh
    index = 0
    path, index = common.decodeString(data, index)
    meshName, index = common.decodeString(data, index)
    positions, index = common.decodeVector3Array(data, index)
    normals, index = common.decodeVector3Array(data, index)
    uvs, index = common.decodeVector2Array(data, index)
    materialIndices, index = common.decodeInt2Array(data, index)
    triangles, index = common.decodeInt3Array(data, index)
    materialNames, index = common.decodeStringArray(data, index)

    bm = bmesh.new()
    verts = []
    for i in range(len(positions)):
        vertex = bm.verts.new(positions[i])
        # according to https://blender.stackexchange.com/questions/49357/bmesh-how-can-i-import-custom-vertex-normals
        # normals are not working for bmesh...
        vertex.normal = normals[i]
        verts.append(vertex)

    uv_layer = None
    if len(uvs) > 0:
        uv_layer = bm.loops.layers.uv.new()

    currentMaterialIndex = 0
    indexInMaterialIndices = 0
    nextriangleIndex = len(triangles)
    if len(materialIndices) > 1:
        nextriangleIndex = materialIndices[indexInMaterialIndices + 1][0]
    if len(materialIndices) > 0:
        currentMaterialIndex = materialIndices[indexInMaterialIndices][1]

    for i in range(len(triangles)):
        if i >= nextriangleIndex:
            indexInMaterialIndices = indexInMaterialIndices + 1
            nextriangleIndex = len(triangles)
            if len(materialIndices) > indexInMaterialIndices + 1:
                nextriangleIndex = materialIndices[indexInMaterialIndices + 1][0]
            currentMaterialIndex = materialIndices[indexInMaterialIndices][1]

        triangle = triangles[i]
        i1 = triangle[0]
        i2 = triangle[1]
        i3 = triangle[2]
        try:
            face = bm.faces.new((verts[i1], verts[i2], verts[i3]))
            face.material_index = currentMaterialIndex
            if uv_layer:
                face.loops[0][uv_layer].uv = uvs[i1]
                face.loops[1][uv_layer].uv = uvs[i2]
                face.loops[2][uv_layer].uv = uvs[i3]
        except:
            pass

    me = client.getOrCreateMesh(meshName)

    bm.to_mesh(me)

    # hack ! Since bmesh cannot be used to set custom normals
    normals2 = []
    for l in me.loops:
        normals2.append(normals[l.vertex_index])
    me.normals_split_custom_set(normals2)
    me.use_auto_smooth = True

    for materialName in materialNames:
        material = client.getOrCreateMaterial(materialName)
        if not materialName in me.materials:
            me.materials.append(material)

    bm.free()
    client.getOrCreateObjectData(path, me)


def decode_layer_float(elmt, layer, data, index):
    elmt[layer], index = common.decodeFloat(data, index)
    return index


def encode_layer_float(elmt, layer):
    return common.encodeFloat(elmt[layer])


def decode_layer_int(elmt, layer, data, index):
    elmt[layer], index = common.decodeInt(data, index)
    return index


def encode_layer_int(elmt, layer):
    return common.encodeInt(elmt[layer])


def decode_bmesh_layer(data, index, layer_collection, element_seq, decode_layer_value_func):
    layer_count, index = common.decodeInt(data, index)
    while layer_count > len(layer_collection):
        if not layer_collection.is_singleton:
            layer_collection.new()
        else:
            layer_collection.verify()  # Will create a layer and returns it
            break  # layer_count should be one but break just in case
    for i in range(layer_count):
        layer = layer_collection[i]
        for elt in element_seq:
            index = decode_layer_value_func(elt, layer, data, index)
    return index


def encode_bmesh_layer(layer_collection, element_seq, encode_layer_value_func):
    binary_buffer = struct.pack('1I', len(layer_collection))
    for i in range(len(layer_collection)):
        layer = layer_collection[i]
        for elt in element_seq:
            binary_buffer += encode_layer_value_func(elt, layer)
    return binary_buffer


def buildSourceMesh(client, data):
    index = 0
    path, index = common.decodeString(data, index)
    meshName, index = common.decodeString(data, index)

    obj = client.getOrCreateObjectData(path, client.getOrCreateMesh(meshName))
    if obj.mode == 'EDIT':
        logger.error("Received a mesh for object %s while begin in EDIT mode, ignoring.", path)
        return

    bm = bmesh.new()

    positions, index = common.decodeVector3Array(data, index)
    logger.debug("Reading %d vertices", len(positions))

    for p in positions:
        bm.verts.new(p)

    bm.verts.ensure_lookup_table()

    index = decode_bmesh_layer(data, index, bm.verts.layers.bevel_weight, bm.verts, decode_layer_float)

    edgeCount, index = common.decodeInt(data, index)
    logger.info("Reading %d edges", edgeCount)

    edgesData = struct.unpack(f'{edgeCount * 4}I', data[index:index + edgeCount * 4 * 4])
    index += edgeCount * 4 * 4

    for edgeIdx in range(edgeCount):
        v1 = edgesData[edgeIdx * 4]
        v2 = edgesData[edgeIdx * 4 + 1]
        edge = bm.edges.new((bm.verts[v1], bm.verts[v2]))
        edge.smooth = bool(edgesData[edgeIdx * 4 + 2])
        edge.seam = bool(edgesData[edgeIdx * 4 + 3])

    index = decode_bmesh_layer(data, index, bm.edges.layers.bevel_weight, bm.edges, decode_layer_float)
    index = decode_bmesh_layer(data, index, bm.edges.layers.crease, bm.edges, decode_layer_float)

    faceCount, index = common.decodeInt(data, index)
    logger.info("Reading %d faces", faceCount)

    for fIdx in range(faceCount):
        materialIdx, index = common.decodeInt(data, index)
        smooth, index = common.decodeBool(data, index)
        vertCount, index = common.decodeInt(data, index)
        faceVertices = struct.unpack(f'{vertCount}I', data[index:index + vertCount * 4])
        index += vertCount * 4
        verts = [bm.verts[i] for i in faceVertices]
        face = bm.faces.new(verts)
        face.material_index = materialIdx
        face.smooth = smooth

    index = decode_bmesh_layer(data, index, bm.faces.layers.face_map, bm.faces, decode_layer_int)

    bm.to_mesh(obj.data)
    bm.free()

    # Load shape keys
    shape_keys_count, index = common.decodeInt(data, index)
    if shape_keys_count > 0:
        obj.shape_key_clear()  # Delete existing ones

        for i in range(shape_keys_count):
            shape_key_name, index = common.decodeString(data, index)
            shape_key = obj.shape_key_add(name=shape_key_name)
            shape_key.mute, index = common.decodeBool(data, index)
            shape_key.value, index = common.decodeFloat(data, index)
            shape_key.slider_min, index = common.decodeFloat(data, index)
            shape_key.slider_max, index = common.decodeFloat(data, index)
            shape_key.vertex_group, index = common.decodeString(data, index)
            shape_key_data_size, index = common.decodeInt(data, index)
            for i in range(shape_key_data_size):
                shape_key.data[i].co = Vector(struct.unpack('3f', data[index:index + 3 * 4]))
                index += 3 * 4
        obj.data.shape_keys.use_relative, index = common.decodeBool(data, index)
        # Set relative keys after all
        for i in range(shape_keys_count):
            relative_key_name, index = common.decodeString(data, index)
            shape_key = obj.data.shape_keys.key_blocks[i]
            shape_key.relative_key = obj.data.shape_keys.key_blocks[relative_key_name]

    materialNames, index = common.decodeStringArray(data, index)
    for materialName in materialNames:
        material = client.getOrCreateMaterial(materialName)
        if not materialName in obj.data.materials:
            obj.data.materials.append(material)


def getMeshBuffers(obj, meshName):
    vertices = []
    normals = []
    uvs = []
    indices = []
    materials = []
    materialIndices = []  # array of triangle index, material index

    # compute modifiers
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj = obj.evaluated_get(depsgraph)

    for slot in obj.material_slots[:]:
        if slot.material:
            materials.append(slot.material.name_full.encode())
        else:
            materials.append("Default".encode())

    # triangulate mesh (before calculating normals)
    mesh = obj.to_mesh()
    if not mesh:
        return None
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()

    # Calculate normals, necessary if auto-smooth option enabled
    mesh.calc_normals()
    mesh.calc_normals_split()
    # calc_loop_triangles resets normals so... don't use it

    # get uv layer
    uvlayer = mesh.uv_layers.active

    currentMaterialIndex = -1
    currentfaceIndex = 0
    for f in mesh.polygons:
        for loop_id in f.loop_indices:
            index = mesh.loops[loop_id].vertex_index
            vertices.extend(mesh.vertices[index].co)
            normals.extend(mesh.loops[loop_id].normal)
            if uvlayer:
                uvs.extend([x for x in uvlayer.data[loop_id].uv])
            indices.append(loop_id)

        if f.material_index != currentMaterialIndex:
            currentMaterialIndex = f.material_index
            materialIndices.append(currentfaceIndex)
            materialIndices.append(currentMaterialIndex)
        currentfaceIndex = currentfaceIndex + 1

    # Vericex count + binary vertices buffer
    size = len(vertices) // 3
    binaryVerticesBuffer = common.intToBytes(
        size, 4) + struct.pack(f'{len(vertices)}f', *vertices)
    # Normals count + binary normals buffer
    size = len(normals) // 3
    binaryNormalsBuffer = common.intToBytes(
        size, 4) + struct.pack(f'{len(normals)}f', *normals)
    # UVs count + binary uvs buffer
    size = len(uvs) // 2
    binaryUVsBuffer = common.intToBytes(
        size, 4) + struct.pack(f'{len(uvs)}f', *uvs)
    # material indices + binary material indices buffer
    size = len(materialIndices) // 2
    binaryMaterialIndicesBuffer = common.intToBytes(
        size, 4) + struct.pack(f'{len(materialIndices)}I', *materialIndices)
    # triangle indices count + binary triangle indices buffer
    size = len(indices) // 3
    binaryIndicesBuffer = common.intToBytes(
        size, 4) + struct.pack(f'{len(indices)}I', *indices)
    # material names count + binary material bnames buffer
    size = len(materials)
    binaryMaterialNames = common.intToBytes(size, 4)
    for material in materials:
        binaryMaterialNames += common.intToBytes(len(material), 4) + material

    return common.encodeString(meshName) + binaryVerticesBuffer + binaryNormalsBuffer + binaryUVsBuffer + binaryMaterialIndicesBuffer + binaryIndicesBuffer + binaryMaterialNames


def dump_mesh(mesh_data):
    # We do not synchronize "select" and "hide" state of mesh elements
    # because we consider them user specific.

    bm = bmesh.new()
    bm.from_mesh(mesh_data)

    logger.debug("Writing %d vertices", len(bm.verts))
    bm.verts.ensure_lookup_table()
    binary_buffer = common.encodeInt(len(bm.verts))
    for vert in bm.verts:
        binary_buffer += struct.pack('3f', *list(vert.co))

    # Vertex layers
    # Ignored layers for now:
    # - skin (BMVertSkin)
    # - deform (BMDeformVert)
    # - paint_mask (float)
    # Other ignored layers:
    # - shape: shape keys are handled with Shape Keys at the mesh and object level
    # - float, int, string: don't really know their role
    binary_buffer += encode_bmesh_layer(bm.verts.layers.bevel_weight, bm.verts, encode_layer_float)

    logger.debug("Writing %d edges", len(bm.edges))
    bm.edges.ensure_lookup_table()
    binary_buffer += common.encodeInt(len(bm.edges))
    for edge in bm.edges:
        binary_buffer += struct.pack('2I', edge.verts[0].index, edge.verts[1].index)
        binary_buffer += struct.pack('1I', edge.smooth)
        binary_buffer += struct.pack('1I', edge.seam)

    # Edge layers
    # Ignored layers for now: None
    # Other ignored layers:
    # - freestyle: of type NotImplementedType, maybe reserved for future dev
    # - float, int, string: don't really know their role
    binary_buffer += encode_bmesh_layer(bm.edges.layers.bevel_weight, bm.edges, encode_layer_float)
    binary_buffer += encode_bmesh_layer(bm.edges.layers.crease, bm.edges, encode_layer_float)

    logger.debug("Writing %d faces", len(bm.faces))
    bm.faces.ensure_lookup_table()
    binary_buffer += common.encodeInt(len(bm.faces))
    for face in bm.faces:
        binary_buffer += common.encodeInt(face.material_index)
        binary_buffer += common.encodeBool(face.smooth)
        binary_buffer += common.encodeInt(len(face.verts))
        for vert in face.verts:
            binary_buffer += common.encodeInt(vert.index)

    # Face layers
    # Ignored layers for now: None
    # Other ignored layers:
    # - freestyle: of type NotImplementedType, maybe reserved for future dev
    # - float, int, string: don't really know their role
    binary_buffer += encode_bmesh_layer(bm.faces.layers.face_map, bm.faces, encode_layer_int)

    # Loops layers
    # A loop is an edge attached to a face (so each edge of a manifold can have 2 loops at most).
    # Ignored layers for now: None
    # Other ignored layers:
    # - float, int, string: don't really know their role

    bm.free()

    # Shape keys
    # source https://blender.stackexchange.com/questions/111661/creating-shape-keys-using-python
    if mesh_data.shape_keys == None:
        binary_buffer += common.encodeInt(0)  # Indicate 0 key blocks
    else:
        logger.debug("Writing %d shape keys", len(mesh_data.shape_keys.key_blocks))
        binary_buffer += common.encodeInt(len(mesh_data.shape_keys.key_blocks))
        for key_block in mesh_data.shape_keys.key_blocks:
            binary_buffer += common.encodeString(key_block.name)
            binary_buffer += common.encodeBool(key_block.mute)
            binary_buffer += common.encodeFloat(key_block.value)
            binary_buffer += common.encodeFloat(key_block.slider_min)
            binary_buffer += common.encodeFloat(key_block.slider_max)
            binary_buffer += common.encodeString(key_block.vertex_group)
            binary_buffer += common.encodeInt(len(key_block.data))
            for i in range(len(key_block.data)):
                binary_buffer += struct.pack('3f', *list(key_block.data[i].co))
        binary_buffer += common.encodeBool(mesh_data.shape_keys.use_relative)
        # Encore relative key names after to facilite loading
        for key_block in mesh_data.shape_keys.key_blocks:
            binary_buffer += common.encodeString(key_block.relative_key.name)

    return binary_buffer


def getSourceMeshBuffers(obj, meshName):
    mesh_data = obj.data
    mesh_binary_buffer = dump_mesh(mesh_data)

    if mesh_data.has_custom_normals:
        # Custom normals are all (0, 0, 0) until calling calc_normals_split() or calc_tangents().
        mesh_data.calc_normals_split()

    materials = []
    for slot in obj.material_slots[:]:
        if slot.material:
            materials.append(slot.material.name_full.encode())
        else:
            materials.append("Default".encode())
    binaryMaterialNames = common.intToBytes(len(materials), 4)
    for material in materials:
        binaryMaterialNames += common.intToBytes(len(material), 4) + material

    return common.encodeString(meshName) + mesh_binary_buffer + binaryMaterialNames