import os
import struct
import zlib
import io
import requests

try:
    from onnxruntime_inference_collection import InferenceSession # Ignore, this only works if you have the onnxruntime_pybind11_state.pyd file for win
except:                                                           # or onnxruntime_pybind11_state.cpython-310-x86_64-linux-gnu.so for linux
    try:
        from onnxruntime import InferenceSession
    except:
        print('ERROR: COULD NOT FIND ONNXRUNTIME INFERENCE SESSION!')
        raise

if os.name == 'nt':
    _temp = os.getenv('temp') or os.getenv('TMP') or os.getenv('TEMP') or '.'
    _model_cache_path = _temp + '/ikabot_pirates_model.onnx'
else:
    _model_cache_path = '/tmp/ikabot_pirates_model.onnx'

_url = 'https://github.com/Ikabot-Collective/IkabotAPI/raw/main/apps/decaptcha/pirates_captcha/yolov8n-ikariam-pirates-mAP-0_989.onnx'
session = None


def _load_model():
    global session
    if session is not None:
        return session

    model_bytes = None

    try:
        if os.path.isfile(_model_cache_path):
            with open(_model_cache_path, 'rb') as f:
                model_bytes = f.read()
    except Exception:
        model_bytes = None

    if model_bytes is None:
        try:
            print('Downloading .onnx model, please wait...')
            resp = requests.get(_url, timeout=30)
            resp.raise_for_status()
            model_bytes = resp.content
            try:
                with open(_model_cache_path, 'wb') as f:
                    f.write(model_bytes)
            except Exception:
                pass
        except Exception:
            model_bytes = None

    if model_bytes is None:
        raise RuntimeError('Failed to load or download the ONNX model')

    session = InferenceSession(model_bytes)
    return session


CLASSES =[
    "B", "2", "D", "X", "5", "M", "W", "A", "7", "4",
    "N", "L", "P", "V", "J", "H", "C", "3", "U", "Q",
    "Y", "S", "T", "K", "R", "E", "G", "F",
]


def read_png(image_bytes):
    with io.BytesIO(image_bytes) as f:
        if f.read(8) != b'\x89PNG\r\n\x1a\n':
            raise ValueError("Not a valid PNG file. Only PNG images are supported.")
        
        palette = None
        idat = bytearray()
        
        while True:
            length_bytes = f.read(4)
            if not length_bytes:
                break
            length = struct.unpack('>I', length_bytes)[0]
            chunk_type = f.read(4)
            chunk_data = f.read(length)
            f.read(4)  # CRC
            
            if chunk_type == b'IHDR':
                width, height, bit_depth, color_type, comp, fltr, interlace = struct.unpack('>IIBBBBB', chunk_data)
                if comp != 0 or fltr != 0:
                    raise ValueError("Unsupported PNG compression or filter method.")
                if interlace != 0:
                    raise ValueError("Interlaced PNGs are not supported.")
                if bit_depth == 16:
                    raise ValueError("16-bit PNGs are not supported.")
            elif chunk_type == b'PLTE':
                palette = chunk_data
            elif chunk_type == b'IDAT':
                idat.extend(chunk_data)
            elif chunk_type == b'IEND':
                break
                
    decompressed = zlib.decompress(idat)
    
    if color_type == 2: bpp = 3
    elif color_type == 6: bpp = 4
    elif color_type in (0, 3): bpp = 1
    else: raise ValueError(f"Unsupported color type {color_type}")
    
    row_bytes = (width * bpp * bit_depth + 7) // 8
    bpp_bytes = max(1, (bpp * bit_depth + 7) // 8)
    
    def paeth(a, b, c):
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        if pa <= pb and pa <= pc: return a
        if pb <= pc: return b
        return c
        
    pixels = bytearray()
    prev_row = bytearray(row_bytes)
    idx = 0
    
    for _ in range(height):
        filter_type = decompressed[idx]
        idx += 1
        row = decompressed[idx : idx + row_bytes]
        idx += row_bytes
        
        unfiltered = bytearray(row_bytes)
        for x in range(row_bytes):
            left = unfiltered[x - bpp_bytes] if x >= bpp_bytes else 0
            up = prev_row[x]
            up_left = prev_row[x - bpp_bytes] if x >= bpp_bytes else 0
            
            if filter_type == 0: val = row[x]
            elif filter_type == 1: val = row[x] + left
            elif filter_type == 2: val = row[x] + up
            elif filter_type == 3: val = row[x] + (left + up) // 2
            elif filter_type == 4: val = row[x] + paeth(left, up, up_left)
            else: raise ValueError(f"Invalid filter type {filter_type}")
            
            unfiltered[x] = val & 0xFF
            
        pixels.extend(unfiltered)
        prev_row = unfiltered
        
    rgb_pixels =[]
    if color_type == 2:  # RGB
        for i in range(0, len(pixels), 3):
            rgb_pixels.append((pixels[i], pixels[i+1], pixels[i+2]))
    elif color_type == 6:  # RGBA
        for i in range(0, len(pixels), 4):
            rgb_pixels.append((pixels[i], pixels[i+1], pixels[i+2]))
    elif color_type == 0:  # Grayscale
        if bit_depth == 8:
            for p in pixels:
                rgb_pixels.append((p, p, p))
        else:
            mask = (1 << bit_depth) - 1
            for i in range(height):
                row_start = i * row_bytes
                row_data = pixels[row_start : row_start + row_bytes]
                x = 0
                for byte in row_data:
                    for shift in range(8 - bit_depth, -1, -bit_depth):
                        if x < width:
                            val = ((byte >> shift) & mask) * 255 // mask
                            rgb_pixels.append((val, val, val))
                            x += 1
    elif color_type == 3:  # Indexed (Palette)
        mask = (1 << bit_depth) - 1
        for i in range(height):
            row_start = i * row_bytes
            row_data = pixels[row_start : row_start + row_bytes]
            x = 0
            for byte in row_data:
                for shift in range(8 - bit_depth, -1, -bit_depth):
                    if x < width:
                        idx_val = (byte >> shift) & mask
                        r = palette[idx_val*3]
                        g = palette[idx_val*3+1]
                        b = palette[idx_val*3+2]
                        rgb_pixels.append((r, g, b))
                        x += 1
                        
    return width, height, rgb_pixels


def _iou(box1, box2):
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[0] + box1[2], box2[0] + box2[2])
    y2 = min(box1[1] + box1[3], box2[1] + box2[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = box1[2] * box1[3] + box2[2] * box2[3] - inter
    return inter / union if union > 0 else 0.0


def _nms(boxes, scores, iou_threshold):
    indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    kept =[]
    while indices:
        current = indices.pop(0)
        kept.append(current)
        indices =[i for i in indices if _iou(boxes[current], boxes[i]) < iou_threshold]
    return kept


def break_ikariam_pirate_captcha(image_bytes):
    width, height, rgb_pixels = read_png(image_bytes)

    assert height <= 100 and width <= 500, "Image is too large"

    # Pad to square with black
    length = max(height, width)
    padded_pixels =[(0, 0, 0)] * (length * length)
    for y in range(height):
        for x in range(width):
            padded_pixels[y * length + x] = rgb_pixels[y * width + x]
            
    scale = length / 640.0

    # Resize to 640x640 using optimized pure-Python Bilinear Interpolation
    r_2d = [[0.0] * 640 for _ in range(640)]
    g_2d = [[0.0] * 640 for _ in range(640)]
    b_2d = [[0.0] * 640 for _ in range(640)]

    x_ratio = length / 640.0
    y_ratio = length / 640.0

    # Precompute coordinates and weights to make the inner loop extremely fast
    x_coords =[]
    for dx in range(640):
        sx = (dx + 0.5) * x_ratio - 0.5
        x0 = max(0, int(sx))
        x1 = min(x0 + 1, length - 1)
        wx = max(0.0, sx - x0)
        x_coords.append((x0, x1, wx, 1.0 - wx))

    y_coords =[]
    for dy in range(640):
        sy = (dy + 0.5) * y_ratio - 0.5
        y0 = max(0, int(sy))
        y1 = min(y0 + 1, length - 1)
        wy = max(0.0, sy - y0)
        y_coords.append((y0 * length, y1 * length, wy, 1.0 - wy))

    # Apply bilinear interpolation and normalize to 0.0 - 1.0
    for dy in range(640):
        y0_len, y1_len, wy, wy_inv = y_coords[dy]
        r_row, g_row, b_row = r_2d[dy], g_2d[dy], b_2d[dy]
        
        for dx in range(640):
            x0, x1, wx, wx_inv = x_coords[dx]
            
            w00 = wx_inv * wy_inv
            w01 = wx * wy_inv
            w10 = wx_inv * wy
            w11 = wx * wy
            
            p00 = padded_pixels[y0_len + x0]
            p01 = padded_pixels[y0_len + x1]
            p10 = padded_pixels[y1_len + x0]
            p11 = padded_pixels[y1_len + x1]
            
            r_row[dx] = (p00[0]*w00 + p01[0]*w01 + p10[0]*w10 + p11[0]*w11) / 255.0
            g_row[dx] = (p00[1]*w00 + p01[1]*w01 + p10[1]*w10 + p11[1]*w11) / 255.0
            b_row[dx] = (p00[2]*w00 + p01[2]*w01 + p10[2]*w10 + p11[2]*w11) / 255.0

    # Build NCHW structure: (1, 3, 640, 640)
    blob = [[r_2d, g_2d, b_2d]]

    # Run inference
    sess = _load_model()
    input_name = sess.get_inputs()[0].name
    raw = sess.run(None, {input_name: blob})[0]

    # Transpose raw[0] from (4+classes, anchors) to (anchors, 4+classes)
    output = list(zip(*raw[0]))

    boxes, scores, class_ids = [], [],[]
    for row in output:
        class_scores = row[4:]
        
        max_score = max(class_scores)
        if max_score < 0.25:
            continue
            
        max_idx = class_scores.index(max_score)
        
        cx, cy, w, h = float(row[0]), float(row[1]), float(row[2]), float(row[3])
        boxes.append([cx - 0.5 * w, cy - 0.5 * h, w, h])
        scores.append(float(max_score))
        class_ids.append(max_idx)

    kept = _nms(boxes, scores, iou_threshold=0.45)

    detections =[]
    for idx in kept:
        detections.append({
            "class_id":   class_ids[idx],
            "class_name": CLASSES[class_ids[idx]],
            "confidence": scores[idx],
            "box":        boxes[idx],
            "scale":      scale,
        })

    detections.sort(key=lambda d: d["box"][0])
    return detections


def get_captcha_string(image_bytes):
    """Return the captcha text for a given image."""
    return "".join(d["class_name"] for d in break_ikariam_pirate_captcha(image_bytes))
