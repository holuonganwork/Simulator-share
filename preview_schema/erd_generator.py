"""ERD Generator: Vis.js Network interactive HTML + Mermaid ERD + SQL DDL generator.

Tương thích toàn diện với SchemaCatalog 51 bảng đa tầng.
Hỗ trợ:
- Lọc tầng kiến trúc trực tiếp trên thanh công cụ Canvas (kể cả khi Toàn Màn Hình).
- Khi bấm vào bảng bất kỳ: Tự động mở rộng hiển thị 100% tất cả các cột của bảng đó.
- Kèm thanh ngăn kéo chi tiết (Side Drawer) hiển thị toàn bộ thuộc tính, kiểu dữ liệu, mô tả và quan hệ.
- Nút bật/tắt mở rộng toàn bộ cột cho mọi bảng.
"""

from __future__ import annotations

import json
from typing import Any
from preview_schema.schema_loader import CATALOG, LAYERS, ALL_RELATIONSHIPS


def generate_vis_network_html(
    selected_tables: list[str] | None = None,
    current_layer: str = "all",
    show_inter_layer_wires: bool = True,
    physics_enabled: bool = True,
) -> str:
    """Tạo trang HTML tương tác độc lập bằng Vis-Network."""
    all_nodes = []
    for t_name, t_info in CATALOG.tables.items():
        layer_key = t_info["layer"]
        layer_meta = LAYERS.get(layer_key, {"color": "#64748B", "icon": "📄"})
        border_color = layer_meta.get("color", "#64748B")
        icon = layer_meta.get("icon", "📄")

        pks = set(t_info["primary_keys"])

        def col_priority(c):
            if c["name"] in pks:
                return 0
            if c["is_fk"]:
                return 1
            if c["is_h3"]:
                return 2
            return 3

        sorted_cols = sorted(t_info["columns"], key=col_priority)

        # Nhãn thu gọn (7 cột đầu + đếm cột còn lại)
        compact_lines = []
        for c in sorted_cols[:7]:
            tag = ""
            if c["name"] in pks:
                tag = " 🔑[PK]"
            elif c["is_fk"]:
                tag = " 🔗[FK]"
            elif c["is_h3"]:
                tag = " ⬢[H3]"
            compact_lines.append(f"• {c['name']}: {c['type']}{tag}")

        if len(sorted_cols) > 7:
            compact_lines.append(f"... (+{len(sorted_cols) - 7} cột khác)")

        # Nhãn đầy đủ (100% tất cả các cột)
        full_lines = []
        for c in sorted_cols:
            tag = ""
            if c["name"] in pks:
                tag = " 🔑[PK]"
            elif c["is_fk"]:
                tag = " 🔗[FK]"
            elif c["is_h3"]:
                tag = " ⬢[H3]"
            full_lines.append(f"• {c['name']}: {c['type']}{tag}")

        header_title = f"{icon} [{layer_key.upper()}] {t_name}"
        col_count_str = f"({t_info['total_columns']} cột"
        if t_info["has_parquet"]:
            col_count_str += " | 📦 Parquet)"
        else:
            col_count_str += ")"

        compact_label = f"{header_title}\n{col_count_str}\n" + "─" * 28 + "\n" + "\n".join(compact_lines)
        full_label = f"{header_title}\n{col_count_str} (Đầy đủ {t_info['total_columns']} cột)\n" + "─" * 28 + "\n" + "\n".join(full_lines)

        cols_summary = []
        for c in t_info["columns"]:
            cols_summary.append({
                "name": c["name"],
                "type": c["type"],
                "required": c["required"],
                "is_pk": c["name"] in pks,
                "is_fk": c["is_fk"],
                "is_h3": c["is_h3"],
                "is_pii": c["is_pii"],
                "description": c["description"],
            })

        all_nodes.append({
            "id": t_name,
            "label": compact_label,
            "compactLabel": compact_label,
            "fullLabel": full_label,
            "layer": layer_key,
            "total_columns": t_info["total_columns"],
            "columns": cols_summary,
            "description": t_info["description"] or "",
            "has_parquet": t_info["has_parquet"],
            "shape": "box",
            "margin": 10,
            "color": {
                "background": "#1E293B",
                "border": border_color,
                "highlight": {
                    "background": "#0F172A",
                    "border": "#38BDF8",
                },
            },
            "font": {
                "color": "#F8FAFC",
                "face": "Consolas, 'Courier New', monospace",
                "size": 11,
                "align": "left",
            },
            "borderWidth": 2.5,
            "shadow": {"enabled": True, "color": "rgba(0,0,0,0.5)", "size": 6, "x": 3, "y": 3},
        })

    # Xây dựng danh sách toàn bộ dây nối (edges)
    all_edges = []
    for idx, rel in enumerate(ALL_RELATIONSHIPS):
        src = rel["source"]
        tgt = rel["target"]
        rel_type = rel.get("type", "inter-layer")

        if not show_inter_layer_wires and rel_type == "inter-layer":
            continue

        is_inter = rel_type == "inter-layer"
        edge_color = "#38BDF8" if is_inter else "#A855F7"
        dash_style = [4, 4] if is_inter else False

        all_edges.append({
            "id": f"edge_{idx}",
            "from": src,
            "to": tgt,
            "label": f"{rel['source_col']} ➔ {rel['target_col']}",
            "font": {
                "color": "#CBD5E1",
                "size": 10,
                "align": "middle",
                "background": "#0F172A",
                "strokeWidth": 0,
            },
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.85}},
            "color": {
                "color": edge_color,
                "highlight": "#FACC15",
                "hover": "#FACC15",
            },
            "dashes": dash_style,
            "smooth": {"type": "cubicBezier", "roundness": 0.4},
            "width": 1.8 if is_inter else 2.2,
            "is_inter": is_inter,
        })

    all_nodes_json = json.dumps(all_nodes, ensure_ascii=False)
    all_edges_json = json.dumps(all_edges, ensure_ascii=False)
    initial_layer = current_layer if current_layer in LAYERS else "all"

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    body {{
      margin: 0;
      padding: 0;
      background-color: #0B1120;
      color: #E2E8F0;
      font-family: system-ui, -apple-system, sans-serif;
      overflow: hidden;
    }}
    #network-container {{
      width: 100vw;
      height: 100vh;
      background-color: #0B1120;
    }}
    .toolbar {{
      position: absolute;
      top: 12px;
      left: 12px;
      z-index: 100;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      background: rgba(15, 23, 42, 0.94);
      backdrop-filter: blur(10px);
      padding: 6px 12px;
      border-radius: 8px;
      border: 1px solid #334155;
      box-shadow: 0 6px 16px rgba(0, 0, 0, 0.5);
    }}
    .toolbar button {{
      background: #1E293B;
      color: #E2E8F0;
      border: 1px solid #475569;
      border-radius: 5px;
      padding: 5px 12px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      transition: all 0.15s ease-in-out;
    }}
    .toolbar button:hover {{
      background: #3B82F6;
      border-color: #60A5FA;
      color: #FFFFFF;
      transform: translateY(-1px);
    }}
    .toolbar button.btn-primary {{
      background: #2563EB;
      border-color: #3B82F6;
      color: #FFFFFF;
      font-weight: 600;
    }}
    .toolbar button.btn-primary:hover {{
      background: #1D4ED8;
      border-color: #93C5FD;
    }}
    .toolbar button.btn-active {{
      background: #0D9488;
      border-color: #14B8A6;
      color: #FFFFFF;
    }}
    .filter-group {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: #0F172A;
      padding: 3px 10px;
      border-radius: 6px;
      border: 1px solid #475569;
      font-size: 12px;
    }}
    .filter-group label {{
      font-weight: 600;
      color: #38BDF8;
      cursor: pointer;
    }}
    .filter-group select {{
      background: #1E293B;
      color: #F8FAFC;
      border: 1px solid #334155;
      border-radius: 4px;
      padding: 4px 8px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      outline: none;
    }}
    .filter-group select:focus {{
      border-color: #38BDF8;
    }}
    .filter-group .chk-label {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      color: #CBD5E1;
      font-size: 11px;
      font-weight: normal;
      margin-left: 4px;
      cursor: pointer;
    }}
    .filter-group input[type="checkbox"] {{
      cursor: pointer;
      accent-color: #2563EB;
    }}
    .legend {{
      position: absolute;
      bottom: 12px;
      right: 12px;
      z-index: 100;
      background: rgba(15, 23, 42, 0.9);
      backdrop-filter: blur(8px);
      padding: 10px 14px;
      border-radius: 8px;
      border: 1px solid #334155;
      font-size: 11px;
      line-height: 1.6;
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .legend-dot {{
      width: 10px;
      height: 10px;
      border-radius: 2px;
    }}
    /* Side drawer chi tiết bảng khi bấm vào */
    .side-drawer {{
      position: absolute;
      top: 12px;
      right: 12px;
      bottom: 12px;
      width: 400px;
      max-width: calc(100vw - 24px);
      background: rgba(15, 23, 42, 0.96);
      backdrop-filter: blur(12px);
      border: 1px solid #334155;
      border-radius: 10px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.7);
      z-index: 200;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      transition: transform 0.25s ease-in-out, opacity 0.25s ease-in-out;
    }}
    .side-drawer.hidden {{
      transform: translateX(120%);
      opacity: 0;
      pointer-events: none;
    }}
    .drawer-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 16px;
      border-bottom: 1px solid #334155;
      background: #1E293B;
    }}
    .drawer-title {{
      font-size: 14px;
      font-weight: 700;
      color: #38BDF8;
    }}
    .drawer-close {{
      background: none;
      border: none;
      color: #94A3B8;
      font-size: 18px;
      cursor: pointer;
      padding: 2px 8px;
      border-radius: 4px;
      line-height: 1;
    }}
    .drawer-close:hover {{
      background: #334155;
      color: #FFFFFF;
    }}
    .drawer-body {{
      padding: 14px;
      overflow-y: auto;
      flex: 1;
      font-size: 12px;
      line-height: 1.5;
    }}
    .drawer-col-item {{
      padding: 8px 10px;
      margin-bottom: 6px;
      background: #1E293B;
      border-radius: 6px;
      border: 1px solid #334155;
    }}
    .drawer-badge-pk {{ background: #EF4444; color: #fff; padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: bold; }}
    .drawer-badge-fk {{ background: #3B82F6; color: #fff; padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: bold; }}
    .drawer-badge-h3 {{ background: #10B981; color: #fff; padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: bold; }}
    .drawer-badge-pii {{ background: #F59E0B; color: #000; padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: bold; }}

    :fullscreen #network-container,
    :-webkit-full-screen #network-container {{
      width: 100vw !important;
      height: 100vh !important;
    }}
  </style>
</head>
<body>
  <div class="toolbar">
    <button class="btn-primary" onclick="toggleFullscreen()" id="btn-fullscreen" title="Phím tắt: F">⛶ Toàn Màn Hình (Fullscreen)</button>
    <button onclick="openStandaloneWindow()" title="Mở sơ đồ trong tab trình duyệt độc lập">🌐 Mở Tab Riêng</button>
    <button onclick="toggleExpandAll()" id="btn-toggle-expand" title="Mở rộng tất cả các cột cho mọi bảng">👁️ Mở Rộng Tất Cả Cột</button>

    <div class="filter-group">
      <label for="layer-select">🏛️ Lọc Tầng:</label>
      <select id="layer-select" onchange="onLayerFilterChange(this.value)">
        <option value="all">🌐 Tất cả (51 bảng)</option>
        <option value="l0">🏛️ L0: Master Reference (5)</option>
        <option value="l1">⚡ L1: Core Event Logs (6)</option>
        <option value="l1r">📦 L1R: Raw GSM Parquet (13)</option>
        <option value="l2">📊 L2: Aggregated State (5)</option>
        <option value="l3">🧠 L3: AI Feature Views (10)</option>
        <option value="advisor">🤖 Advisor: AI Decisions (12)</option>
      </select>
      <label class="chk-label" title="Khi lọc theo tầng, hiển thị thêm các bảng bên ngoài có dây nối tới tầng này">
        <input type="checkbox" id="chk-connected" onchange="onLayerFilterChange(document.getElementById('layer-select').value)">
        + Bảng liên kết ngoài
      </label>
    </div>

    <button onclick="network.fit({{animation: true}})">🎯 Vừa màn hình (Fit)</button>
    <button onclick="togglePhysics()">⚡ Bật/Tắt Tự Sắp Xếp</button>
    <button onclick="network.moveTo({{scale: network.getScale() * 1.25}})">🔍 Phóng to</button>
    <button onclick="network.moveTo({{scale: network.getScale() * 0.8}})">🔍 Thu nhỏ</button>
  </div>

  <div class="legend">
    <div style="font-weight:bold; margin-bottom:4px; color:#38BDF8;">Phân Tầng CSDL:</div>
    <div class="legend-item"><span class="legend-dot" style="background:#6366F1;"></span> L0: Master Reference</div>
    <div class="legend-item"><span class="legend-dot" style="background:#0D9488;"></span> L1: Core Event Logs</div>
    <div class="legend-item"><span class="legend-dot" style="background:#2563EB;"></span> L1R: Raw GSM Parquet</div>
    <div class="legend-item"><span class="legend-dot" style="background:#D97706;"></span> L2: Aggregated State</div>
    <div class="legend-item"><span class="legend-dot" style="background:#9333EA;"></span> L3: AI Feature Views</div>
    <div class="legend-item"><span class="legend-dot" style="background:#E11D48;"></span> Advisor: AI Decisions</div>
    <div style="margin-top:4px; border-top:1px solid #334155; padding-top:4px; color:#94A3B8;">
      ── Nét liền: Nội bộ tầng<br>
      ┈┈ Nét đứt: Liên kết xuyên tầng
    </div>
  </div>

  <div id="side-drawer" class="side-drawer hidden">
    <div class="drawer-header">
      <div id="drawer-title" class="drawer-title"></div>
      <button class="drawer-close" onclick="deselectNode()" title="Đóng bảng chi tiết">✕</button>
    </div>
    <div id="drawer-body" class="drawer-body"></div>
  </div>

  <div id="network-container"></div>

  <script type="text/javascript">
    const allNodesData = {all_nodes_json};
    const allEdgesData = {all_edges_json};

    const allNodesMap = {{}};
    allNodesData.forEach(n => allNodesMap[n.id] = n);

    const nodes = new vis.DataSet();
    const edges = new vis.DataSet();

    const container = document.getElementById('network-container');
    const data = {{ nodes: nodes, edges: edges }};

    let physicsActive = {'true' if physics_enabled else 'false'};
    let expandAllActive = false;
    let currentlySelectedNodeId = null;

    const options = {{
      physics: {{
        enabled: physicsActive,
        solver: 'barnesHut',
        barnesHut: {{
          gravitationalConstant: -3500,
          centralGravity: 0.25,
          springLength: 220,
          springConstant: 0.04,
          damping: 0.12,
          avoidOverlap: 0.85
        }},
        stabilization: {{
          enabled: true,
          iterations: 120,
          updateInterval: 25
        }}
      }},
      interaction: {{
        dragNodes: true,
        dragView: true,
        zoomView: true,
        hover: true,
        multiselect: true,
        navigationButtons: false
      }}
    }};

    const network = new vis.Network(container, data, options);

    function onLayerFilterChange(selectedLayer) {{
      const chk = document.getElementById('chk-connected');
      const includeConnected = chk ? chk.checked : false;

      let activeNodeIds = new Set();
      let filteredNodes = [];

      if (selectedLayer === 'all') {{
        filteredNodes = allNodesData;
        activeNodeIds = new Set(allNodesData.map(n => n.id));
      }} else {{
        allNodesData.forEach(n => {{
          if (n.layer === selectedLayer) {{
            activeNodeIds.add(n.id);
          }}
        }});

        if (includeConnected) {{
          allEdgesData.forEach(e => {{
            if (activeNodeIds.has(e.from)) activeNodeIds.add(e.to);
            if (activeNodeIds.has(e.to)) activeNodeIds.add(e.from);
          }});
        }}

        filteredNodes = allNodesData.filter(n => activeNodeIds.has(n.id));
      }}

      const filteredEdges = allEdgesData.filter(e =>
        activeNodeIds.has(e.from) && activeNodeIds.has(e.to)
      );

      // Reset node labels based on current expandAllActive mode
      filteredNodes.forEach(n => {{
        n.label = expandAllActive ? n.fullLabel : n.compactLabel;
      }});

      nodes.clear();
      nodes.add(filteredNodes);

      edges.clear();
      edges.add(filteredEdges);

      currentlySelectedNodeId = null;
      closeSideDrawer();

      const nCount = filteredNodes.length;
      network.setOptions({{
        physics: {{
          enabled: true,
          solver: 'barnesHut',
          barnesHut: {{
            gravitationalConstant: nCount <= 10 ? -2500 : (nCount <= 20 ? -4000 : -6500),
            centralGravity: 0.25,
            springLength: nCount <= 10 ? 180 : (nCount <= 20 ? 240 : 320),
            springConstant: 0.04,
            damping: 0.12,
            avoidOverlap: 0.85
          }}
        }}
      }});

      setTimeout(() => {{
        network.fit({{animation: {{duration: 400, easingFunction: 'easeInOutQuad'}}}});
      }}, 150);

      setTimeout(() => {{
        network.setOptions({{ physics: {{ enabled: false }} }});
      }}, 1200);
    }}

    // Khởi tạo tầng ban đầu
    const initialLayer = '{initial_layer}';
    const layerSelect = document.getElementById('layer-select');
    if (layerSelect) {{
      layerSelect.value = initialLayer;
    }}
    onLayerFilterChange(initialLayer);

    function selectNode(nodeId) {{
      const nodeData = allNodesMap[nodeId];
      if (!nodeData) return;

      // 1. Nếu chưa ở chế độ mở rộng tất cả, thu gọn node cũ và mở rộng node mới được click
      if (!expandAllActive) {{
        if (currentlySelectedNodeId && currentlySelectedNodeId !== nodeId) {{
          const prev = allNodesMap[currentlySelectedNodeId];
          if (prev) {{
            nodes.update({{ id: currentlySelectedNodeId, label: prev.compactLabel }});
          }}
        }}
        // MỞ RỘNG TOÀN BỘ CỘT CỦA BẢNG ĐƯỢC CLICK!
        nodes.update({{ id: nodeId, label: nodeData.fullLabel }});
        currentlySelectedNodeId = nodeId;
      }}

      // 2. Làm nổi bật các node có liên kết, làm mờ node khác
      const connectedNodes = network.getConnectedNodes(nodeId);
      connectedNodes.push(nodeId);

      nodes.forEach(function(node) {{
        if (connectedNodes.includes(node.id)) {{
          nodes.update({{ id: node.id, opacity: 1.0 }});
        }} else {{
          nodes.update({{ id: node.id, opacity: 0.25 }});
        }}
      }});

      // 3. Mở side drawer hiển thị chi tiết đầy đủ
      openSideDrawer(nodeData);
    }}

    function deselectNode() {{
      if (!expandAllActive && currentlySelectedNodeId) {{
        const prev = allNodesMap[currentlySelectedNodeId];
        if (prev) {{
          nodes.update({{ id: currentlySelectedNodeId, label: prev.compactLabel }});
        }}
        currentlySelectedNodeId = null;
      }}

      nodes.forEach(function(node) {{
        nodes.update({{ id: node.id, opacity: 1.0 }});
      }});

      closeSideDrawer();
    }}

    function toggleExpandAll() {{
      expandAllActive = !expandAllActive;
      const btn = document.getElementById('btn-toggle-expand');
      if (btn) {{
        if (expandAllActive) {{
          btn.innerHTML = "📁 Thu Gọn Cột (Compact)";
          btn.classList.add("btn-active");
        }} else {{
          btn.innerHTML = "👁️ Mở Rộng Tất Cả Cột";
          btn.classList.remove("btn-active");
        }}
      }}
      nodes.forEach(function(node) {{
        const data = allNodesMap[node.id];
        if (data) {{
          nodes.update({{ id: node.id, label: expandAllActive ? data.fullLabel : data.compactLabel }});
        }}
      }});
    }}

    function openSideDrawer(nodeData) {{
      const drawer = document.getElementById('side-drawer');
      const title = document.getElementById('drawer-title');
      const body = document.getElementById('drawer-body');
      if (!drawer || !title || !body) return;

      title.innerHTML = `${{nodeData.label.split('\\n')[0]}} <span style="font-size:11px;color:#94A3B8;">(${{nodeData.columns.length}} cột)</span>`;

      let html = '';
      if (nodeData.description) {{
        html += `<div style="margin-bottom:12px;padding:8px 10px;background:#0F172A;border-radius:6px;border-left:3px solid #38BDF8;color:#CBD5E1;">
          <b>Mô tả:</b> ${{nodeData.description}}
        </div>`;
      }}

      html += `<div style="font-weight:600;margin-bottom:8px;color:#F8FAFC;">Danh Sách Toàn Bộ ${{nodeData.columns.length}} Cột:</div>`;

      nodeData.columns.forEach((c, idx) => {{
        let badges = '';
        if (c.is_pk) badges += ' <span class="drawer-badge-pk">[PK]</span>';
        if (c.is_fk) badges += ' <span class="drawer-badge-fk">[FK]</span>';
        if (c.is_h3) badges += ' <span class="drawer-badge-h3">[H3]</span>';
        if (c.is_pii) badges += ' <span class="drawer-badge-pii">[PII]</span>';

        const reqBadge = c.required ? '<span style="color:#EF4444;font-size:10px;">(NOT NULL)</span>' : '<span style="color:#64748B;font-size:10px;">(NULL)</span>';

        html += `<div class="drawer-col-item">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-weight:600;color:#38BDF8;font-family:Consolas,monospace;">${{idx + 1}}. ${{c.name}}</span>
            <span>${{badges}}</span>
          </div>
          <div style="margin-top:2px;color:#94A3B8;font-size:11px;">
            Kiểu: <code style="color:#FACC15;">${{c.type}}</code> ${{reqBadge}}
          </div>
          ${{c.description ? `<div style="margin-top:4px;color:#E2E8F0;font-size:11px;">${{c.description}}</div>` : ''}}
        </div>`;
      }});

      body.innerHTML = html;
      drawer.classList.remove('hidden');
    }}

    function closeSideDrawer() {{
      const drawer = document.getElementById('side-drawer');
      if (drawer) drawer.classList.add('hidden');
    }}

    // Khi click vào 1 node trên canvas
    network.on('click', function(params) {{
      if (params.nodes.length > 0) {{
        selectNode(params.nodes[0]);
      }} else {{
        deselectNode();
      }}
    }});

    function togglePhysics() {{
      physicsActive = !physicsActive;
      network.setOptions({{ physics: {{ enabled: physicsActive }} }});
    }}

    function toggleFullscreen() {{
      const btn = document.getElementById('btn-fullscreen');
      const isFull = document.fullscreenElement || 
                     (window.parent && window.parent.document && window.parent.document.fullscreenElement);

      if (!isFull) {{
        let el = document.documentElement;
        try {{
          if (window.parent && window.parent.document) {{
            const iframes = window.parent.document.getElementsByTagName('iframe');
            for (let i = 0; i < iframes.length; i++) {{
              if (iframes[i].contentDocument === document) {{
                el = iframes[i];
                break;
              }}
            }}
          }}
        }} catch(e) {{}}

        const req = el.requestFullscreen || el.webkitRequestFullscreen || el.mozRequestFullScreen || el.msRequestFullscreen;
        if (req) {{
          req.call(el).then(() => {{
            if (btn) btn.innerHTML = "✖ Thu Nhỏ (Exit)";
            setTimeout(() => {{ network.redraw(); network.fit({{animation: true}}); }}, 200);
          }}).catch(err => {{
            document.documentElement.requestFullscreen().then(() => {{
              if (btn) btn.innerHTML = "✖ Thu Nhỏ (Exit)";
              setTimeout(() => {{ network.redraw(); network.fit({{animation: true}}); }}, 200);
            }}).catch(e => {{
              openStandaloneWindow();
            }});
          }});
        }} else {{
          openStandaloneWindow();
        }}
      }} else {{
        try {{
          if (document.exitFullscreen) document.exitFullscreen();
          else if (window.parent && window.parent.document && window.parent.document.exitFullscreen) {{
            window.parent.document.exitFullscreen();
          }}
        }} catch(e) {{}}
        if (btn) btn.innerHTML = "⛶ Toàn Màn Hình (Fullscreen)";
        setTimeout(() => {{ network.redraw(); network.fit({{animation: true}}); }}, 200);
      }}
    }}

    function openStandaloneWindow() {{
      const htmlContent = '<!DOCTYPE html>' + document.documentElement.outerHTML;
      const blob = new Blob([htmlContent], {{ type: 'text/html;charset=utf-8' }});
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
    }}

    function handleFsChange() {{
      const btn = document.getElementById('btn-fullscreen');
      const isFull = document.fullscreenElement || 
                     (window.parent && window.parent.document && window.parent.document.fullscreenElement);
      if (btn) {{
        btn.innerHTML = isFull ? "✖ Thu Nhỏ (Exit)" : "⛶ Toàn Màn Hình (Fullscreen)";
      }}
      setTimeout(() => {{
        network.redraw();
        network.fit({{animation: true}});
      }}, 150);
    }}

    document.addEventListener('fullscreenchange', handleFsChange);
    document.addEventListener('webkitfullscreenchange', handleFsChange);
    try {{
      if (window.parent && window.parent.document) {{
        window.parent.document.addEventListener('fullscreenchange', handleFsChange);
      }}
    }} catch(e) {{}}

    document.addEventListener('keydown', function(e) {{
      if (e.key === 'f' || e.key === 'F') {{
        if (document.activeElement && ['input', 'textarea'].includes(document.activeElement.tagName.toLowerCase())) return;
        toggleFullscreen();
      }}
    }});
  </script>
</body>
</html>
"""
    return html


def generate_mermaid_erd(selected_tables: list[str]) -> str:
    """Sinh chuỗi Mermaid erDiagram chuẩn cho các bảng được chọn."""
    lines = ["erDiagram"]
    tbl_set = set(selected_tables)

    for t_name in selected_tables:
        t_info = CATALOG.get_table(t_name)
        if not t_info:
            continue

        pks = set(t_info["primary_keys"])
        clean_tbl_name = t_name.replace("-", "_")
        lines.append(f"    {clean_tbl_name} {{")

        for col in t_info["columns"][:12]:
            cname = col["name"].replace("-", "_")
            ctype = col["type"].replace(" ", "_").replace("/", "_")
            tag = "PK" if cname in pks else ("FK" if col["is_fk"] else "")
            lines.append(f"        {ctype} {cname} {tag}")

        lines.append("    }")

    # Thêm các quan hệ
    relationships = CATALOG.get_relationships_for_selection(selected_tables)
    for rel in relationships:
        src = rel["source"].replace("-", "_")
        tgt = rel["target"].replace("-", "_")
        col_label = f"{rel['source_col']}_to_{rel['target_col']}"
        lines.append(f"    {src} ||--o{{ {tgt} : \"{col_label}\"")

    return "\n".join(lines)


def generate_sql_ddl(table_name: str) -> str:
    """Sinh câu lệnh SQL CREATE TABLE chi tiết cho bảng được chọn."""
    t_info = CATALOG.get_table(table_name)
    if not t_info:
        return f"-- Bảng '{table_name}' không tồn tại trong catalog."

    layer = t_info["layer"].upper()
    layer_meta = LAYERS.get(t_info["layer"], {})
    layer_title = layer_meta.get("title", "")
    lines = [
        f"-- ========================================================",
        f"-- BẢNG: {table_name}",
        f"-- TẦNG KIẾN TRÚC: {layer} ({layer_title})",
        f"-- SCHEMA VERSION: {t_info['schema_version']}",
        f"-- TỔNG SỐ CỘT: {t_info['total_columns']}",
        f"-- MÔ TẢ: {t_info['description'] or 'Không có mô tả'}",
        f"-- ========================================================",
        f"CREATE TABLE {table_name} (",
    ]

    col_defs = []
    pks = set(t_info["primary_keys"])

    type_map = {
        "string": "VARCHAR(255)",
        "integer": "BIGINT",
        "number": "NUMERIC(12, 4)",
        "boolean": "BOOLEAN",
        "array": "JSONB",
        "object": "JSONB",
    }

    for col in t_info["columns"]:
        cname = col["name"]
        raw_type = col["type"]

        # Chuyển đổi sang kiểu SQL tương ứng
        if "time" in cname or cname in ("created_at", "updated_at", "t", "t_now"):
            sql_type = "TIMESTAMPTZ"
        elif "date" in cname:
            sql_type = "DATE"
        elif cname.endswith("_id") or cname == "id":
            sql_type = "VARCHAR(64)"
        elif "h3" in cname or "hex" in cname:
            sql_type = "VARCHAR(16)"
        elif "vnd" in cname or "amount" in cname or "fare" in cname:
            sql_type = "NUMERIC(14, 2)"
        else:
            sql_type = type_map.get(raw_type, "VARCHAR(255)")

        nullability = "NOT NULL" if col["required"] else "NULL"
        comment = ""
        if cname in pks:
            comment += " -- [PRIMARY KEY]"
        elif col["is_fk"]:
            comment += " -- [FOREIGN KEY]"
        elif col["is_h3"]:
            comment += " -- [H3 SPATIAL INDEX]"

        if col["description"]:
            clean_desc = col["description"][:60].replace("\n", " ")
            comment += f" ({clean_desc})"

        col_defs.append(f"    {cname:<32} {sql_type:<16} {nullability}{comment}")

    # Thêm ràng buộc khóa chính
    if pks:
        pk_cols = ", ".join(pks)
        col_defs.append(f"    CONSTRAINT pk_{table_name} PRIMARY KEY ({pk_cols})")

    lines.append(",\n".join(col_defs))
    lines.append(");")
    lines.append("")

    # Thêm comment bảng
    if t_info["description"]:
        lines.append(f"COMMENT ON TABLE {table_name} IS '{t_info['description'][:150]}';")

    return "\n".join(lines)
