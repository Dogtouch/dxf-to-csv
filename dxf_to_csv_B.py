"""
DXF → 注文伝票CSV 変換ツール（Windows ドラッグ＆ドロップ対応版）
=================================================================
使い方:
  DXFファイルをこのexe（またはpyファイル）にドラッグ＆ドロップする
  → 同じフォルダに「元のファイル名_注文伝票.csv」が生成される

読み取り順序:
  図面の上の行から順に、各行は左から右へ読み取ります

縦・横の順番:
  常に「小さい値 → 大きい値」の順で出力します
"""

import re
import csv
import sys
import os
import math
import tkinter as tk
from tkinter import messagebox


# ============================================================
# DXFパース
# ============================================================

def parse_dxf(filepath):
    with open(filepath, 'r', encoding='shift_jis', errors='replace') as f:
        content = f.read()

    entities_match = re.search(r'ENTITIES(.*?)ENDSEC', content, re.DOTALL)
    if not entities_match:
        raise ValueError("ENTITIESセクションが見つかりません。")

    blocks = re.split(r'(?:^|\r?\n)\s*0\r?\n', entities_match.group(1))
    result = []
    for block in blocks:
        lines = block.replace('\r\n', '\n').strip().split('\n')
        if not lines:
            continue
        etype = lines[0].strip()
        if etype not in ('LINE', 'TEXT', 'ARC'):
            continue
        entity = {'type': etype}
        i = 1
        while i < len(lines) - 1:
            code = lines[i].strip()
            val  = lines[i + 1].strip() if i + 1 < len(lines) else ''
            try:
                if   code == '8':  entity['layer'] = val
                elif code == '62': entity['color'] = int(val)
                elif code == '1':  entity['text']  = val
                elif code == '10': entity['x']     = float(val)  # 始点X（ARCは中心X）
                elif code == '20': entity['y']     = float(val)  # 始点Y（ARCは中心Y）
                elif code == '11': entity['x2']    = float(val)  # 終点X（LINE用）
                elif code == '21': entity['y2']    = float(val)  # 終点Y（LINE用）
                elif code == '40': entity['r']     = float(val)  # 半径（ARC用）
                elif code == '50': entity['a1']    = float(val)  # 開始角（ARC用）
                elif code == '51': entity['a2']    = float(val)  # 終了角（ARC用）
            except Exception:
                pass
            i += 2
        result.append(entity)
    return result


# ============================================================
# 図形グループ化・情報抽出
# ============================================================

def group_by_row(marks, tolerance=300):
    """Y座標が近いものを同じ行にまとめ、上→下・左→右の順に整列"""
    sorted_marks = sorted(marks, key=lambda t: -t.get('y', 0))
    rows = []
    for mt in sorted_marks:
        y = mt.get('y', 0)
        placed = False
        for row in rows:
            row_y = sum(m.get('y', 0) for m in row) / len(row)
            if abs(y - row_y) < tolerance:
                row.append(mt)
                placed = True
                break
        if not placed:
            rows.append([mt])
    for row in rows:
        row.sort(key=lambda t: t.get('x', 0))
    return rows


def texts_near(cx, cy, all_texts, radius=80):
    """
    図形マークのごく近く（半径80以内）のテキストだけを拾う。
    図形同士が近接していても、隣の図形のテキストを誤って拾わないようにするため、
    範囲を狭く設定している（自分の情報は距離35以内、隣は最低でも105以上離れている）。
    """
    return [t for t in all_texts
            if abs(t.get('x', 0) - cx) < radius and abs(t.get('y', 0) - cy) < radius]


# ============================================================
# 形状判定（四角形＝寸法切り / それ以外＝概型切り）
# ============================================================

def _arc_endpoints(e):
    """円弧の両端点（始点・終点）を計算して返す。

    DXFの円弧は「中心(x,y)・半径r・開始角a1・終了角a2」で表される。
    端点 = 中心から、その角度方向に半径ぶん進んだ点。
    これを使うと、外形線としてつながっている円弧だけを正しく連結でき、
    無関係な穴の小円弧を巻き込まなくなる。
    """
    cx, cy, r = e.get('x', 0), e.get('y', 0), e.get('r', 0)
    a1 = e.get('a1', 0)
    a2 = e.get('a2', 0)
    p1 = (cx + r * math.cos(math.radians(a1)),
          cy + r * math.sin(math.radians(a1)))
    p2 = (cx + r * math.cos(math.radians(a2)),
          cy + r * math.sin(math.radians(a2)))
    return [p1, p2]


def build_figures(entities):
    """
    赤い外形線（LINE/ARC）を、端点がつながっているものどうしで
    1つの図形にまとめる（連結成分）。各図形の構成要素を保持する。
    """
    red_lines = [e for e in entities if e['type'] == 'LINE' and e.get('color') == 1]
    red_arcs  = [e for e in entities if e['type'] == 'ARC'  and e.get('color') == 1]

    segs = []
    for e in red_lines:
        p1 = (e.get('x', 0), e.get('y', 0))
        p2 = (e.get('x2', 0), e.get('y2', 0))
        segs.append({'ends': [p1, p2], 'kind': 'LINE', 'ent': e})
    for e in red_arcs:
        # 円弧の両端点を計算してつなぐ（外形線としてつながった円弧だけを連結）
        ends = _arc_endpoints(e)
        segs.append({'ends': ends, 'kind': 'ARC', 'ent': e})

    TOL = 2.0
    n = len(segs)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # すべて端点どうしの一致で連結判定する（円弧も端点を計算済みなので統一）
    def near(a, b, tol):
        return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol

    for i in range(n):
        for j in range(i + 1, n):
            connected = False
            for e1 in segs[i]['ends']:
                for e2 in segs[j]['ends']:
                    if near(e1, e2, TOL):
                        connected = True
                        break
                if connected:
                    break
            if connected:
                parent[find(i)] = find(j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(segs[i])

    figures = []
    for members in groups.values():
        # 図形の中心位置を、構成する線分の端点から計算
        pts = []
        for m in members:
            if m['kind'] == 'LINE':
                e = m['ent']
                pts.append((e.get('x', 0), e.get('y', 0)))
                pts.append((e.get('x2', 0), e.get('y2', 0)))
            else:
                pts.extend(_arc_endpoints(m['ent']))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        figures.append({
            'members': members,
            'cx': (min(xs) + max(xs)) / 2,
            'cy': (min(ys) + max(ys)) / 2,
        })
    return figures


def is_rectangle(fig):
    """
    純粋な長方形かどうかを判定する。
    条件：円弧を含まず、直線がちょうど4本で、水平2本＋垂直2本（直角）。
    """
    lines = [m for m in fig['members'] if m['kind'] == 'LINE']
    arcs  = [m for m in fig['members'] if m['kind'] == 'ARC']

    if arcs:
        return False
    if len(lines) != 4:
        return False

    EPS = 2.0
    horizontal = vertical = 0
    for m in lines:
        e = m['ent']
        dx = abs(e.get('x2', 0) - e.get('x', 0))
        dy = abs(e.get('y2', 0) - e.get('y', 0))
        if dy < EPS:
            horizontal += 1
        elif dx < EPS:
            vertical += 1
    return horizontal == 2 and vertical == 2


def shape_label(mark_x, mark_y, figures):
    """
    図形マークに最も近い図形を探し、四角形なら「寸」、それ以外なら「型」を返す。
    （寸＝寸法切り、型＝概型切り）
    """
    if not figures:
        return ''
    best = min(figures, key=lambda f: (f['cx'] - mark_x) ** 2 + (f['cy'] - mark_y) ** 2)
    return '寸' if is_rectangle(best) else '型'


def extract_parts(entities):
    texts      = [e for e in entities if e['type'] == 'TEXT']
    mark_texts = [t for t in texts if re.match(r'<.+>\d+枚', t.get('text', ''))]

    if not mark_texts:
        raise ValueError("図形マーク（<マーク>n枚 形式）のテキストが見つかりませんでした。")

    ordered = [mt for row in group_by_row(mark_texts) for mt in row]

    # 赤い外形線から図形を組み立てる（形状判定に使う）
    figures = build_figures(entities)

    rows = []
    for mt in ordered:
        cx, cy    = mt.get('x', 0), mt.get('y', 0)
        text_vals = [t.get('text', '') for t in texts_near(cx, cy, texts)]

        m           = re.match(r'<(.+)>(\d+)枚', mt.get('text', ''))
        figure_mark = m.group(1) if m else ''
        quantity    = m.group(2) if m else ''

        # マークの接頭辞（最初のハイフンまで）を削除： T-1C53 → 1C53
        mark_short = figure_mark.split('-', 1)[1] if '-' in figure_mark else figure_mark

        material    = next((t for t in text_vals if re.match(r'^(SM|SS|SN|S[A-Z])\d', t)), '')
        size_text   = next((t for t in text_vals if re.match(r'^PL-', t)), '')

        # サイズは「PL-」だけを消して、元の並び順のまま： PL-9x100x100 → 9x100x100
        size = size_text[3:] if size_text.startswith('PL-') else size_text

        # 形状判定：四角形なら「寸」（寸法切り）、それ以外なら「型」（概型切り）
        shape = shape_label(cx, cy, figures)

        rows.append({
            'マーク': mark_short,
            '規格':   material,
            'サイズ': size,
            '枚数':   quantity,
            '形状':   shape,
        })
    return rows


# ============================================================
# CSV保存
# ============================================================

def save_csv(rows, output_path):
    fieldnames = ['マーク', '規格', 'サイズ', '枚数', '形状']
    with open(output_path, 'w', newline='', encoding='shift_jis', errors='replace') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# メイン処理
# ============================================================

def process(dxf_path):
    entities = parse_dxf(dxf_path)
    rows     = extract_parts(entities)
    base     = os.path.splitext(dxf_path)[0]
    out_path = base + "_部品リストB.csv"
    save_csv(rows, out_path)
    return len(rows), out_path


def main():
    root = tk.Tk()
    root.withdraw()

    dxf_files = sys.argv[1:]

    if not dxf_files:
        messagebox.showinfo(
            "DXF → CSV 変換ツール",
            "使い方：\nDXFファイルをこのアイコンにドラッグ＆ドロップしてください。\n\n"
            "同じフォルダに「ファイル名_注文伝票.csv」が生成されます。"
        )
        return

    results = []
    errors  = []

    for dxf_path in dxf_files:
        dxf_path = dxf_path.strip('"')
        if not os.path.exists(dxf_path):
            errors.append(f"ファイルが見つかりません：\n{dxf_path}")
            continue
        try:
            count, out_path = process(dxf_path)
            results.append(f"✅ {os.path.basename(dxf_path)}\n   → {count}件 → {os.path.basename(out_path)}")
        except Exception as e:
            errors.append(f"❌ {os.path.basename(dxf_path)}\n   エラー: {e}")

    msg_parts = []
    if results:
        msg_parts.append("【完了】\n" + "\n\n".join(results))
    if errors:
        msg_parts.append("【エラー】\n" + "\n\n".join(errors))

    messagebox.showinfo("DXF → CSV 変換ツール", "\n\n".join(msg_parts))


if __name__ == '__main__':
    main()
