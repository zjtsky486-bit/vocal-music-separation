# Vocal Music Separation

把 **任意正在发声的 Windows 应用** 接入本地人声／音乐分离。提供 Node.js / Electron 主进程 API、独立双路音量、真实频谱、自动音源选择、手动来源切换与退出恢复。

公开源码仓库为 [vocal-music-separation](https://github.com/zjtsky486-bit/vocal-music-separation)，同时包含供其他 AI 读取的 [SKILL.md](skills/vocal-music-core/SKILL.md)、[AGENTS.md](AGENTS.md) 和 [llms.txt](llms.txt)。将仓库链接或技能文件交给支持读取代码的 AI，它可以按文档安装前置组件并把功能接入用户选择的软件；上传本身不会使所有 AI 自动发现仓库。`audio-plugin.json` 是本项目的机器可读能力说明，不是未经验证的商店／市场注册格式。Node.js 包名保持 `vocal-music-core`。

## 能做什么

- 自动发现浏览器、音乐播放器、游戏等应用的活动音频，不再限定网易云。
- 多个应用发声时列出来源，按 PID 与创建时间选择；自动模式保持连续来源，避免瞬时提示音抢走播放。
- 上层独立控制人声，下层独立控制音乐；两路均为 0–100%，默认 100%。
- 本地 RoFormer GPU 分离、净化与真实频谱。两路同值时保留原声混音细节；单独人声／音乐仍使用净化声部。
- Windows 音频路由独占、平滑音量、父进程死亡及正常退出恢复。C 盘可用空间达到 40 GiB 时停止。

音源检测依据应用音频活动，不能语义区分一首歌与电影对白、通知或游戏音效。该功能包含实际播放延迟，也不能保证所有电音、混响人声都能完全分离。浏览器版本与 GPU 能力影响接入和连续播放，应在目标机器测试。

## 前置组件

Windows x64 build 20348+（推荐 Windows 11），Node.js 20+，Python **3.12 x64**，可用 CUDA 或 DirectML GPU，以及另行安装的 [VB-CABLE](https://vb-audio.com/Cable/)。生产不需要 PyTorch。

源码不包含 Python／CUDA 运行库、音频驱动或 922 MB 模型。当前经过验证的模型是 **Kim Mel-Band RoFormer T241**，不是上游 release 的 T801，也不是本机其他项目的 StemgenRT。具体身份、许可和导出方式见 [模型说明](docs/models.md)。

## 安装

克隆公开仓库后，在仓库目录操作：

```powershell
git clone https://github.com/zjtsky486-bit/vocal-music-separation.git
cd vocal-music-separation

# 先查看安装计划：不会安装依赖或改变音频设备。
powershell -File scripts/install-runtime.ps1 -Backend cuda -DryRun

# 已安装 Python 3.12 时创建仓库内 venv、下载固定版本运行依赖。
powershell -File scripts/install-runtime.ps1 -Backend cuda

# AMD / Intel / DirectX GPU 可选另一独立环境的 DirectML 后端。
# 在新的干净克隆里执行，不要把两个同名 ORT build 装进同一 venv。
powershell -File scripts/install-runtime.ps1 -Backend dml
```

`-PythonPath` 可指定已经安装的 Python 3.12 x64；未指定时用 `py -3.12`。`-RuntimeDir` 可选择仓库内的独立 venv 路径，随后给 SDK 的 `pythonPath` 传其 `Scripts/python.exe`。安装器不会全局安装 Python、自动安装驱动或把 CUDA/DML 混装。

已有经过验证的 T241 文件时，向安装器追加 `-ModelPath`，或运行：

```powershell
python scripts/import-model.py --model .\mel_band_roformer_kim_T241.onnx
```

它校验固定的生产摘要后复制到引擎。没有模型时使用 [导出入口](scripts/export-model.py) 和 [模型说明](docs/models.md)，从固定版本的上游与已校验 MIT checkpoint 生成兼容 T241；导出环境与生产环境分开。没有公开、已经验证的 T241 ONNX 直链，不会下载一个不同模型冒充它。

诊断安装：

```powershell
npm run doctor
# DirectML 环境：
$env:AUDIO_BACKEND = 'dml'
npm run doctor
```

缺少模型、驱动或 GPU 时，doctor 返回实际缺项；不录音、不接管系统输出，不伪装连接成功。

## 接入软件

在宿主项目安装本地源码目录或生成的 `.tgz`，然后在 Node／Electron **主进程**调用：

```js
const path = require('node:path');
const { createAudioService } = require('vocal-music-core');

const audio = createAudioService({
  runtimeDir: path.resolve('data/audio'),
  backend: 'cuda'
});
audio.on('state', state => {
  // ready 为 true 才开放实际调音；error/busy/来源状态显示给用户。
  console.log(state.phase, state.sources, state.telemetry);
});

async function start() {
  const report = await audio.diagnose();
  if (!report.ok) throw new Error(JSON.stringify(report));
  audio.setGains({ vocalGain: 1, instrumentalGain: 1 });
  audio.start();
}

async function close() {
  await audio.stop(); // 先恢复原输出，再允许宿主关闭。
}

module.exports = { audio, start, close };
```

`engineDir` 与 `pythonPath` 可使用宿主自己的引擎／环境绝对路径。可写状态留在宿主 `runtimeDir`，不要把 npm 模块的安装目录当作用户设置目录。

```js
audio.setGains({ vocalGain: 1, instrumentalGain: 0 }); // 单独人声
audio.setGains({ vocalGain: 0, instrumentalGain: 1 }); // 单独音乐
audio.setGains({ vocalGain: 0, instrumentalGain: 0 }); // 静音
audio.selectSource({ pid: null });                   // 自动音源

const source = audio.getSources()[0];
if (source) audio.selectSource({ pid: source.pid, createdAt: source.createdAt });
```

完整接口见 [API](docs/api.md)。可运行 Node 示例为 `npm run demo`；Electron 主进程与安全 preload 参考 [examples](examples/)。任意 UI 框架都可使用这些接口，两路音量必须各自独立，不能把它们重新做成 `1 - value` 的一对比例。

## AI 集成入口

技能位于 `skills/vocal-music-core`，按对应 AI 工具的技能加载方式导入。它指导代理在用户目标范围内诊断依赖、接入接口、创建上下两路控制、显示来源／异常，并验证停止恢复。保留既有会话的授权，不重复请求已经批准的步骤；未授权的系统驱动安装或发布仍是单独的系统／外部操作。

## 测试与 GitHub 发布

```powershell
npm test
python tests/audio-source-selector.py
python tests/handoff-guard.py
python -m compileall -q plugins/music scripts
python scripts/check-release.py
npm pack
```

Node 测试使用模拟子进程验证真实 SDK 的参数、分块 Unicode 状态解析、双路保存、源身份、busy、停止等待与空间保护，不重路由用户音频。音源选择器另有不依赖 GPU 的用例。GitHub Actions 会执行轻量检查；真实 GPU 音质／延迟与恢复需要在目标 Windows 机器验证，不能从这些 mock 测试推断。

发布更新前保持 `.gitignore`，不要加入模型、运行目录、个人设置、音频恢复记录或用户歌曲。检查通过后再提交源码更新或生成源码 ZIP。本包不内置发布凭据。

本项目代码使用 MIT；第三方代码和未捆绑依赖的许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
