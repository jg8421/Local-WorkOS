# Windows 换机便携部署

下载通用包并完整解压到普通本机文件夹，双击 `启动.cmd`（与 `Start-WorkOS.cmd` 相同）。不需要管理员、预装Python或运行pip；窗口先显示本机能力，再打开应用。不要在ZIP内直接双击，也不要只取一个启动文件。`环境检查.cmd`/`Check-Environment.cmd`只检查，`Optional-Setup.cmd`仅在你明确选项后打开官方依赖页面，不自动安装。

## 两种包

| 包 | 已包含 | 外部条件 |
|---|---|---|
| core | 应用/资料库/会议编辑/HTML/Markdown、官方便携Python、PDF文字解析源码 | AI需要已配置模型服务；Word/PPT/高级Excel导出使用full |
| full | core全部、离线python-docx/python-pptx/openpyxl及其固定依赖 | 投资回报原生重算需要本机Microsoft Excel；不要求Node/artifact-tool |

包里不含任何个人项目、资料、数据库、备份、API key、DSH登录或机器配置。运行数据在本用户`%LOCALAPPDATA%/LocalWorkOS`；换机业务数据恢复与软件部署是两件事，需你另行导入自己的备份/资料。初次启动只开放本机回环，不读取旧机器的公网/OneDrive/记忆配置，不创建开机启动，不覆盖其他程序。若本机已有WorkOS运行，复用该实例而不强制替换；需要升级安装时另走发布SOP。

## 能力检查与缺依赖

- 默认模型是WorkBuddy DeepSeek V4.1 Flash。软件包不包含WorkBuddy服务/账号；在设置检测已有桥接或登记兼容服务。没有模型服务时本地资料管理、原文检索及已有记录编辑仍可用。
- 投资回报：full内的openpyxl便携作者生成新工作簿，本机Excel实际重算后才标为Excel已核验。检查注册表仅表示检测到安装，不代表一次计算已成功；无Excel保留假设，不生成假的已算结果，不打开或修改原始项目工作簿。
- GPT / DSH可选，需本人安装DSH和Node并登录。不迁移DSH账号文件，不保证仅发现二进制就拥有某模型权限。若构建时选择`--with-node`，包会额外包含官方Node，仍不包含DSH或登录。
- 纪要PDF需要已经授权的LibreOffice Kit及其Node配置；不会打包宿主SDK或从未知地址找替代。full可先导出Word。复杂原表的自动改写/三表整合不是部署包增加的功能。

可选依赖页面：[Python官方Windows下载](https://www.python.org/downloads/windows/)、[Node官方](https://nodejs.org/en/download)、[Microsoft Excel](https://www.microsoft.com/microsoft-365/excel)。便携包已经提供Python，通常无需安装系统Python。

## 构建、校验与许可

以下命令在构建机的Git源码checkout内运行；下载和打包不安装系统Python，不改系统PATH。

```powershell
python tools/package_windows.py --profile core --output <fresh-output-directory>
python tools/package_windows.py --profile full --output <fresh-output-directory>
```

构建只使用Git已跟踪的公开运行源码和明确的部署辅助文件白名单，不复制宿主Python、用户site-packages、凭证、tests或work目录。下载仅允许Python官方/PyPI官方文件/Node官方域名，`deployment/runtime-lock.json`固定版本、文件名、SHA256与摘要来源；每次复核哈希，错误即停止。Python摘要来自官方发布Sigstore bundle的message digest，wheel摘要来自固定版本PyPI元数据；哈希比对不额外宣称已独立验证Sigstore签名身份。

ZIP附`package-manifest.json`记录每个文件的SHA256、源码revision、是否有未提交变化和依赖版本，旁边有整个ZIP的`.sha256`。包内保留Python LICENSE、各wheel dist-info的许可证和pypdf许可证；不包含不明授权的宿主artifact-tool、Office或DSH运行目录。公开源代码沿用README的许可边界。

正式包默认只允许clean Git HEAD，并在构建完成前再次核对。开发预览须显式加`--allow-dirty`，文件名带`-preview`且manifest记为preview，不能当成已发布版本；源码HEAD在构建期间改变会立即失败。

构建机需网络下载官方运行库，下载缓存留在忽略的`work/package-cache`；新机器核心启动和导出无需网络安装。模型服务调用本身仍可能需要联网。目标是Windows x64、Python3.13 ABI；ARM64/32位需另做匹配的锁和测试，不能冒充通用包。

便携解释器的`_pth`只允许包内路径，不运行宿主用户的site-packages或`.pth`扩展；双击启动也使用`-I`。可选Node只加到本次子进程PATH，不写系统或用户环境变量。

隔离验证使用解压后的便携解释器和临时端口/数据目录：

```powershell
./runtime/python/python.exe ./app/tools/deployment_preflight.py --json
./runtime/python/python.exe ./app/tools/deployment_preflight.py --launch --no-browser --port 18879 --data-dir <new-temporary-directory>
```

生产默认18866不能用作写入测试；只停止本次创建的测试进程，不停止现有用户Excel或其他WorkOS。

交付包的自动验证在源码checkout运行`python -m tools.verify_portable <ZIP> --work <scratch-directory> --report <report.json>`：核对所有文件哈希，直接用包内Python在无全局Python/Node/DSH的PATH下启动空个人区，检查只读HTTP接口与full导出。此smoke不调用模型、不执行Excel原生重算；该计算验收另由财务测试负责。
