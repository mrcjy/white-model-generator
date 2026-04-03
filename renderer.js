/* renderer.js — 渲染进程逻辑 */

const elShpPath   = document.getElementById('shp-path');
const elOutPath   = document.getElementById('out-path');
const elField     = document.getElementById('height-field');
const elDefH      = document.getElementById('default-height');
const elElev      = document.getElementById('elevation-offset');
const elLog       = document.getElementById('log');
const elSpinner   = document.getElementById('spinner');
const elStatus    = document.getElementById('status-text');
const btnShp      = document.getElementById('btn-shp');
const btnOut      = document.getElementById('btn-out');
const btnGenerate = document.getElementById('btn-generate');
const btnClear    = document.getElementById('btn-clear');

const elProgressSection = document.getElementById('progress-section');
const elProgFill        = document.getElementById('prog-fill');
const elProgTiles       = document.getElementById('prog-tiles');
const elProgRate        = document.getElementById('prog-rate');
const elProgElapsed     = document.getElementById('prog-elapsed');
const elProgEta         = document.getElementById('prog-eta');

let shpPath = null;
let outPath = null;

// ── 进度追踪状态 ──────────────────────────────────────────────────────────────

const prog = {
  active:        false,
  startTime:     0,
  totalBldgs:    0,
  tilesGenerated:0,
  estimatedTotal:0,  // 根据建筑数估算，随生成动态修正
  maxPerTile:    500,
  timer:         null,
};

function fmtTime(seconds) {
  const s = Math.floor(seconds);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}:${String(m % 60).padStart(2,'0')}:${String(s % 60).padStart(2,'0')}`;
  return `${String(m).padStart(2,'0')}:${String(s % 60).padStart(2,'0')}`;
}

function updateProgressUI() {
  const elapsed = (Date.now() - prog.startTime) / 1000;
  elProgElapsed.textContent = fmtTime(elapsed);

  const n = prog.tilesGenerated;
  const est = prog.estimatedTotal;

  elProgTiles.textContent = est > 0
    ? `${n.toLocaleString()} / ~${est.toLocaleString()} tiles`
    : `${n.toLocaleString()} tiles`;

  if (n > 0 && elapsed > 1) {
    const rate = n / elapsed;
    elProgRate.textContent = `${rate.toFixed(1)} tiles/s`;

    if (est > n) {
      const remaining = (est - n) / rate;
      elProgEta.textContent = `预计剩余 ${fmtTime(remaining)}`;
    } else {
      elProgEta.textContent = '';
    }

    // 进度百分比，最多到 95%（防止在未知总量时过早达到 100%）
    const pct = Math.min(n / Math.max(est, n + 1) * 100, 95);
    elProgFill.style.width = pct + '%';
    elProgFill.classList.remove('indeterminate');
  }
}

function startProgress(maxPerTile) {
  prog.active         = true;
  prog.startTime      = Date.now();
  prog.totalBldgs     = 0;
  prog.tilesGenerated = 0;
  prog.estimatedTotal = 0;
  prog.maxPerTile     = maxPerTile || 500;

  elProgFill.style.width = '0%';
  elProgFill.classList.add('indeterminate');
  elProgTiles.textContent  = '0 tiles';
  elProgRate.textContent   = '';
  elProgElapsed.textContent= '00:00';
  elProgEta.textContent    = '';
  elProgressSection.style.display = 'flex';

  prog.timer = setInterval(updateProgressUI, 1000);
}

function finishProgress(ok) {
  prog.active = false;
  clearInterval(prog.timer);
  prog.timer = null;

  updateProgressUI();  // 最终刷新一次

  if (ok) {
    elProgFill.classList.remove('indeterminate');
    elProgFill.style.width = '100%';
    elProgFill.style.background = '#4ec9a0';
    elProgEta.textContent = '已完成';
  } else {
    elProgFill.classList.remove('indeterminate');
    elProgFill.style.background = '#e06c75';
    elProgEta.textContent = '生成失败';
  }
}

function resetProgress() {
  elProgressSection.style.display = 'none';
  elProgFill.style.width = '0%';
  elProgFill.style.background = '#3a7bd5';
  elProgFill.classList.remove('indeterminate');
}

// ── 工具函数 ──────────────────────────────────────────────────────────────────

function appendLog(text, type = 'info') {
  const line = document.createElement('div');
  line.className = 'log-' + type;
  line.textContent = text;
  elLog.appendChild(line);
  elLog.scrollTop = elLog.scrollHeight;
}

function setStatus(text, busy = false) {
  elStatus.textContent = text;
  elSpinner.style.display = busy ? 'block' : 'none';
}

function classifyLine(line) {
  if (/\[stderr\]/.test(line)) {
    if (/error|traceback|错误|失败/i.test(line)) return 'err';
    return 'warn';
  }
  if (/error|错误|失败/i.test(line)) return 'err';
  if (/warn|警告/i.test(line))        return 'warn';
  if (/✅|done|完成/i.test(line))      return 'ok';
  return 'info';
}

function checkReady() {
  btnGenerate.disabled = !(shpPath && outPath && elField.value);
}

// ── 选择 SHP 文件 ─────────────────────────────────────────────────────────────

btnShp.addEventListener('click', async () => {
  const p = await window.api.openShp();
  if (!p) return;

  shpPath = p;
  elShpPath.textContent = p;
  elShpPath.classList.remove('placeholder');
  elField.disabled = true;
  elField.innerHTML = '<option value="">正在读取字段…</option>';
  setStatus('读取字段…', true);

  try {
    const fields = await window.api.getFields(p);
    elField.innerHTML = '';

    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '── 请选择高度字段 ──';
    elField.appendChild(empty);

    fields
      .filter(f => f !== 'geometry')
      .forEach(f => {
        const opt = document.createElement('option');
        opt.value = f;
        opt.textContent = f;
        if (/height|高度|floor|楼高/i.test(f)) opt.selected = true;
        elField.appendChild(opt);
      });

    elField.disabled = false;
    setStatus(`读取到 ${fields.length} 个字段`);
    appendLog(`SHP 字段: ${fields.filter(f => f !== 'geometry').join(', ')}`, 'info');
  } catch (err) {
    setStatus('读取字段失败');
    appendLog('读取字段失败: ' + err.message, 'err');
    elField.innerHTML = '<option value="">读取失败，请重试</option>';
  }

  checkReady();
});

// ── 选择输出目录 ───────────────────────────────────────────────────────────────

btnOut.addEventListener('click', async () => {
  const p = await window.api.openDir();
  if (!p) return;
  outPath = p;
  elOutPath.textContent = p;
  elOutPath.classList.remove('placeholder');
  checkReady();
});

// ── 字段选择变化 ───────────────────────────────────────────────────────────────

elField.addEventListener('change', checkReady);

// ── 清空日志 ───────────────────────────────────────────────────────────────────

btnClear.addEventListener('click', () => { elLog.innerHTML = ''; });

// ── 生成 ───────────────────────────────────────────────────────────────────────

btnGenerate.addEventListener('click', async () => {
  if (!shpPath || !outPath || !elField.value) return;

  const opts = {
    input:           shpPath,
    output:          outPath,
    heightField:     elField.value,
    defaultHeight:   parseFloat(elDefH.value)   || 10,
    elevationOffset: parseFloat(elElev.value)   || 15,
    maxPerTile:      500,
  };

  // 锁定 UI，启动进度
  btnGenerate.disabled = true;
  btnShp.disabled      = true;
  btnOut.disabled      = true;
  elField.disabled     = true;
  resetProgress();
  startProgress(opts.maxPerTile);
  setStatus('正在生成…', true);
  appendLog('▶ 开始生成', 'info');
  appendLog(`  输入: ${opts.input}`, 'info');
  appendLog(`  输出: ${opts.output}`, 'info');
  appendLog(`  高度字段: ${opts.heightField}  默认高度: ${opts.defaultHeight}m  海拔偏移: ${opts.elevationOffset}m`, 'info');

  try {
    await window.api.generate(opts);
    finishProgress(true);
    setStatus('生成完成 ✓');
    appendLog('✅ 生成完成！输出目录: ' + opts.output, 'ok');
  } catch (err) {
    finishProgress(false);
    setStatus('生成失败');
    appendLog('❌ 失败: ' + err.message, 'err');
  }

  // 解锁 UI
  btnGenerate.disabled = false;
  btnShp.disabled      = false;
  btnOut.disabled      = false;
  elField.disabled     = false;
  checkReady();
});

// ── 接收日志流 ────────────────────────────────────────────────────────────────

window.api.onLog((line) => {
  appendLog(line, classifyLine(line));

  if (!prog.active) return;

  // 匹配建筑总数: "28,872 buildings"
  const mBldg = line.match(/([\d,]+)\s+buildings/i);
  if (mBldg) {
    prog.totalBldgs = parseInt(mBldg[1].replace(/,/g, ''), 10);
    // 粗估总 tile 数：建筑数 / 每块上限 * 四叉树系数(约3.5)
    prog.estimatedTotal = Math.ceil(prog.totalBldgs / prog.maxPerTile * 3.5);
    setStatus(`共 ${mBldg[1]} 栋建筑，四叉树切分中…`, true);
    return;
  }

  // 匹配生成进度: "Generated 400 tiles (depth=5)..."
  const mTile = line.match(/Generated\s+([\d,]+)\s+tiles/i);
  if (mTile) {
    const n = parseInt(mTile[1].replace(/,/g, ''), 10);
    prog.tilesGenerated = n;
    // 如果实际值超过估算，动态扩大上限（避免进度条倒退）
    if (n > prog.estimatedTotal * 0.9) {
      prog.estimatedTotal = Math.ceil(n * 1.3);
    }
    updateProgressUI();
    setStatus(`已生成 ${mTile[1]} 个 tile…`, true);
    return;
  }

  // 匹配最终 tile 总数: "Total tiles : 194"
  const mTotal = line.match(/Total tiles\s*[:：]\s*([\d,]+)/i);
  if (mTotal) {
    prog.tilesGenerated = parseInt(mTotal[1].replace(/,/g, ''), 10);
    prog.estimatedTotal = prog.tilesGenerated;
    updateProgressUI();
  }
});

window.api.onDone((_ok) => {
  // done 信号已在 generate promise 里处理
});
