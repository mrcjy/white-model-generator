const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs   = require('fs');
const { spawn } = require('child_process');

// ── 直接解析 DBF 文件头获取字段名（零 Python 启动开销）────────────────────────
function readDbfFields(shpPath) {
  const dbfPath = shpPath.replace(/\.shp$/i, '.dbf');
  const buf = fs.readFileSync(dbfPath);

  // DBF header: offset 8 = header size (uint16 LE)
  // Field descriptors start at offset 32, each 32 bytes
  // Byte 0-10 of descriptor = field name (null-padded ASCII)
  // Header ends with 0x0D terminator byte
  const headerSize = buf.readUInt16LE(8);
  const fields = [];

  for (let offset = 32; offset < headerSize - 1; offset += 32) {
    if (buf[offset] === 0x0D) break;
    const name = buf.toString('ascii', offset, offset + 11).replace(/\0/g, '').trim();
    if (name) fields.push(name);
  }

  return fields;
}

let mainWindow;
let generateProcess = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 800,
    height: 700,
    minWidth: 640,
    minHeight: 560,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: '3D Tiles 白膜生成器',
  });

  mainWindow.loadFile('index.html');
  mainWindow.setMenuBarVisibility(false);
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// ── 选择 SHP 文件 ─────────────────────────────────────────────────────────────
ipcMain.handle('dialog:openShp', async () => {
  const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
    title: '选择建筑轮廓 Shapefile',
    filters: [{ name: 'Shapefile', extensions: ['shp'] }],
    properties: ['openFile'],
  });
  return canceled ? null : filePaths[0];
});

// ── 选择输出目录 ───────────────────────────────────────────────────────────────
ipcMain.handle('dialog:openDir', async () => {
  const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
    title: '选择输出目录',
    properties: ['openDirectory', 'createDirectory'],
  });
  return canceled ? null : filePaths[0];
});

// ── 读取 SHP 字段列表（直接解析 DBF，无需启动 Python）──────────────────────────
ipcMain.handle('shp:getFields', (_event, shpPath) => {
  try {
    const fields = readDbfFields(shpPath);
    return fields;
  } catch (err) {
    throw new Error('读取 DBF 字段失败: ' + err.message);
  }
});

// ── 根据运行环境选择 Python 调用方式 ──────────────────────────────────────────
// 打包后: resources/python/gen_3dtiles_lod.exe（PyInstaller 编译产物）
// 开发时: uv run --no-project gen_3dtiles_lod.py
function spawnPython(scriptArgs) {
  const spawnEnv = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };

  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, 'python', 'gen_3dtiles_lod.exe');
    return spawn(exe, scriptArgs, { env: spawnEnv });
  }

  const scriptPath = path.join(__dirname, 'gen_3dtiles_lod.py');
  return spawn('uv', ['run', '--no-project', scriptPath, ...scriptArgs], {
    cwd: __dirname,
    env: spawnEnv,
  });
}

// ── 生成 3D Tiles ─────────────────────────────────────────────────────────────
ipcMain.handle('tiles:generate', (_event, opts) => {
  return new Promise((resolve, reject) => {
    if (generateProcess) {
      reject(new Error('已有任务正在运行'));
      return;
    }

    const scriptArgs = [
      '--input',           opts.input,
      '--output',          opts.output,
      '--height-field',    opts.heightField,
      '--default-height',  String(opts.defaultHeight),
      '--elevation-offset',String(opts.elevationOffset),
      '--max-per-tile',    String(opts.maxPerTile),
    ];

    generateProcess = spawnPython(scriptArgs);

    const send = (line) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('tiles:log', line);
      }
    };

    generateProcess.stdout.on('data', (d) => {
      d.toString().split('\n').forEach(line => { if (line) send(line); });
    });
    generateProcess.stderr.on('data', (d) => {
      d.toString().split('\n').forEach(line => { if (line) send('[stderr] ' + line); });
    });

    generateProcess.on('close', (code) => {
      generateProcess = null;
      const ok = code === 0;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('tiles:done', ok);
      }
      ok ? resolve() : reject(new Error(`生成失败，退出码 ${code}`));
    });

    generateProcess.on('error', (err) => {
      generateProcess = null;
      const hint = app.isPackaged
        ? '启动 Python 失败: ' + err.message
        : '启动 uv 失败，请确认已安装 uv: ' + err.message;
      reject(new Error(hint));
    });
  });
});
