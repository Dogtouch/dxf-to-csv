"""
DXF → 注文伝票CSV 変換ツール（Windows ドラッグ＆ドロップ対応版）
=================================================================
使い方:
  DXFファイルをこのexe（またはpyファイル）にドラッグ＆ドロップする
  → 同じフォルダに「元のファイル名_注文伝票.csv」が生成される

読み取り順序:
  図面の上の行から順に、各行は左から右へ読み取ります
"""

import re
import csv
import sys
import os
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
        if etype not in ('LINE', 'TEXT'):
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
                elif code == '10': entity['x']     = float(val)
                elif code == '20': entity['y']     = float(val)
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


def texts_near(cx, cy, all_texts, radius=350):
    return [t for t in all_texts
            if abs(t.get('x', 0) - cx) < radius and abs(t.get('y', 0) - cy) < radius]


def extract_parts(entities):
    texts      = [e for e in entities if e['type'] == 'TEXT']
    mark_texts = [t for t in texts if re.match(r'<.+>\d+枚', t.get('text', ''))]

    if not mark_texts:
        raise ValueError("図形マーク（<マーク>n枚 形式）のテキストが見つかりませんでした。")

    ordered = [mt for row in group_by_row(mark_texts) for mt in row]

    rows = []
    for mt in ordered:
        cx, cy    = mt.get('x', 0), mt.get('y', 0)
        text_vals = [t.get('text', '') for t in texts_near(cx, cy, texts)]

        m             = re.match(r'<(.+)>(\d+)枚', mt.get('text', ''))
        figure_mark   = m.group(1) if m else ''
        quantity      = m.group(2) if m else ''
        koji_mark     = next((t for t in text_vals if re.match(r'^\([A-Z0-9\-]+\)$', t)), '')
        material      = next((t for t in text_vals if re.match(r'^(SM|SS|SN|S[A-Z])\d', t)), '')
        size_text     = next((t for t in text_vals if re.match(r'^PL-', t)), '')
        thickness = width = length = ''
        if size_text:
            sm = re.match(r'PL-(\d+\.?\d*)x(\d+\.?\d*)x(\d+\.?\d*)', size_text)
            if sm:
                thickness, width, length = sm.group(1), sm.group(2), sm.group(3)
        hole = next((t for t in text_vals
                     if '孔=' in t or ('φ' in t and t != material)), '')

        rows.append({
            '工事マーク': koji_mark,
            '材質':       material,
            '厚み':       thickness,
            '縦':         width,
            '横':         length,
            '図形マーク': figure_mark,
            '枚数':       quantity,
            '穴径':       hole,
        })
    return rows


# ============================================================
# CSV保存
# ============================================================

def save_csv(rows, output_path):
    fieldnames = ['工事マーク', '材質', '厚み', '縦', '横', '図形マーク', '枚数', '穴径']
    with open(output_path, 'w', newline='', encoding='shift_jis', errors='replace') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# メイン処理
# ============================================================

def process(dxf_path):
    """1つのDXFファイルを処理してCSVを出力する"""
    entities  = parse_dxf(dxf_path)
    rows      = extract_parts(entities)
    base      = os.path.splitext(dxf_path)[0]
    out_path  = base + "_注文伝票.csv"
    save_csv(rows, out_path)
    return len(rows), out_path


def main():
    # tkinterのルートウィンドウ（メッセージ表示用・画面には出さない）
    root = tk.Tk()
    root.withdraw()

    # ドラッグ＆ドロップ or コマンドライン引数でDXFパスを受け取る
    dxf_files = sys.argv[1:]

    if not dxf_files:
        messagebox.showinfo(
            "DXF → CSV 変換ツール",
            "使い方：\nDXFファイルをこのアイコンにドラッグ＆ドロップしてください。\n\n"
            "同じフォルダに「ファイル名_注文伝票.csv」が生成されます。"
        )
        return

    results  = []
    errors   = []

    for dxf_path in dxf_files:
        # Windowsがパスを引用符つきで渡す場合に対応
        dxf_path = dxf_path.strip('"')

        if not os.path.exists(dxf_path):
            errors.append(f"ファイルが見つかりません：\n{dxf_path}")
            continue

        try:
            count, out_path = process(dxf_path)
            results.append(f"✅ {os.path.basename(dxf_path)}\n   → {count}件 → {os.path.basename(out_path)}")
        except Exception as e:
            errors.append(f"❌ {os.path.basename(dxf_path)}\n   エラー: {e}")

    # 結果をポップアップ表示
    msg_parts = []
    if results:
        msg_parts.append("【完了】\n" + "\n\n".join(results))
    if errors:
        msg_parts.append("【エラー】\n" + "\n\n".join(errors))

    messagebox.showinfo("DXF → CSV 変換ツール", "\n\n".join(msg_parts))


if __name__ == '__main__':
    main()
