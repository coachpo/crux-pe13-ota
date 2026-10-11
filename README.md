# Crux PE13 OTA

为 Xiaomi Mi 9 Pro 5G（`crux`）的 `aosp_crux_release-user` 公共测试候选版提供 OTA 版本信息。ROM 使用原厂 ABL、官方分区和 non-A/B 全量 ZIP。

- [下载与安装说明](https://github.com/coachpo/crux-pe13-ota/releases)：选择对应版本的签名 ROM ZIP、匹配的公共 PE Recovery 及安装指南。测试范围、已知问题和兼容升级起点以该版本说明为准。
- [OTA 页面](https://coachpo.github.io/crux-pe13-ota/)
- [原生更新接口](https://coachpo.github.io/crux-pe13-ota/updates/crux/thirteen_plus.json)

安装前备份数据，并保持 bootloader 解锁。首次安装和救援按版本指南操作；保留数据升级只适用于指南确认的 v2 数据版本。OTA 安装能力的验证不代表硬件功能或日用稳定性全部通过。

## 发布新版本

下列命令从仓库根目录执行，使用 Python 3 标准库。输入必须是同一发布版本的最终签名 `target_files` 和签名全量 OTA ZIP。

1. 准备本地发布记录；此步不改变推荐版本。

   ```sh
   python3 scripts/prepare_release.py \
     --signed-target-files /path/to/signed-target_files.zip \
     --ota /path/to/ota.zip \
     --tag RELEASE_TAG \
     --filename ROM_FILENAME.zip \
     --site-dir site \
     --repository coachpo/crux-pe13-ota
   ```

   可选参数为 `--forum-url HTTPS_URL` 和 `--website-url HTTPS_URL`。准备工具核对包的版本与设备信息，生成本地输出及 `site/releases/<sha256>.json` 历史记录。原生 `datetime` 来自 `org.pixelexperience.build_date_utc`，OTA 的 `post-timestamp` 另行记录；两者不要求相等。`filehash` 使用 MD5，`id` 使用 ZIP 的 SHA256；包的信任仍由 OTA 签名验证建立。

2. 在 `coachpo/crux-pe13-ota` 创建对应不可变标签的 GitHub **prerelease**，上传签名全量 ZIP、匹配的公共 PE Recovery 和安装指南。ROM ZIP 的上传文件名必须与 `--filename` 一致。确认所有文件已可公开下载，且下载内容与准备时的输入一致。已公布的标签及其 ROM 文件保持不变；修订包使用新标签。

3. 上传完成后保存 Release 的实际文件清单，再使用准备输出中的 SHA256 推荐该版本，并验证完整站点。

   ```sh
   gh release view RELEASE_TAG --repo coachpo/crux-pe13-ota \
     --json tagName,assets > /tmp/uploaded-release.json
   python3 scripts/recommend_release.py \
     --release-id SHA256 \
     --uploaded-release-json /tmp/uploaded-release.json \
     --site-dir site
   python3 scripts/validate_site.py --site-dir site
   ```

   推荐命令核对上传文件的名称、URL 和大小；清单提供非空 SHA256 digest 时还会核对其身份，再更新 `site/updates/crux/thirteen_plus.json`。该路由保持 PE 客户端使用的单对象协议：`datetime`、`filename`、`id`、`size`、`url`、`version`、`filehash`，其中 `version` 为 `thirteen_plus`。要切换回一个已归档版本，重新获取该标签的文件清单，使用其 SHA256 再运行推荐命令，并检查版本兼容性。

4. 复核后提交并推送站点和发布记录，由 GitHub Pages workflow 校验并部署 `site/`。也可手动触发 workflow。**先上传并确认 Release 文件，再部署指向它的推荐接口。**部署后检查公开接口及 ROM 下载，随后在支持的升级起点进行实际 OTA 验收。

仓库和 Pages 只保存公共站点、版本信息及工具。私钥、签名 `target_files`、ROM/Recovery 二进制和私人构建输入均不进入 Git；`target_files` 与匹配源码、符号作为私有发布来源留存。ROM 与 Recovery 通过 GitHub Releases 分发。

## 串联发布与中断后继续

`publish_release.py` 串联上述准备、上传核验和本地推荐步骤。输入须先通过独立原生签名与内容门禁；脚本不执行 ROM 编译或签名。Recovery 必须与签名 `target_files` 中 `IMAGES/recovery.img` 的实际字节相同。

```sh
python3 -B scripts/publish_release.py \
  --signed-target-files /private/release/signed-target_files.zip \
  --ota /private/release/ota.zip \
  --recovery /private/release/Crux-PE-Recovery.img \
  --release-notes /private/release/release-notes.md \
  --guide /private/release/INSTALL.md \
  --tag RELEASE_TAG \
  --filename Crux-PE13-RELEASE_TAG.zip \
  --site-dir site \
  --evidence-dir /private/release/publication
```

默认仅准备：保留不可变发布历史，在仓库外的 `--evidence-dir/assets/` 暂存公共 ROM、Recovery、版本说明及安装指南，并输出 `publication-plan.json` 和具体 GitHub 命令；推荐接口保持原版本。OTA 的上传名由 `--filename` 指定，其余文件使用各自文件名，名称不得重复。可重复添加 `--extra-asset /path/to/public-file`，例如同源 fastboot 救援包。私钥及任何 `target_files` ZIP 均拒绝进入公共暂存资产。

确认发布输入后，在同一命令末尾添加 `--publish`。此步需要已登录且有该仓库发布权限的 `gh`：标签不存在时创建新的 prerelease；已有标签时仅继续相同的公开 prerelease，核对已有文件，再上传缺失文件。已有资产名称必须全部属于本次暂存清单，Release 正文必须与计划中固定的版本说明完全一致；发现其他包、未知资产或不同正文时停止。已有文件的 URL、大小或 SHA256 不符时也停止；不会覆盖或删除文件、修改既有版本说明或把旧标签用于新包。GitHub 未提供资产 digest 时，脚本实际下载该文件核验 SHA256。上传中断可用相同输入与证据目录重跑，已核验的相同文件会复用。

全部公共文件上传并核验后，脚本才推荐本地接口并验证站点。失败保留原推荐；证据目录保存 `publication-receipt.json`，完整上传后还保存实际 GitHub API 清单 `github-release.json` 和适用于推荐工具的 `uploaded-release.json`。成功状态 `published_and_recommended_locally` 表示 Release 资产已上传且本地推荐已更新；随后复核 diff、提交并推送站点，由 Pages workflow 部署，再检查公开接口与设备 OTA。脚本不提交、推送或部署站点。

## 源码

- [设备树与发布打包工具](https://github.com/coachpo/device_xiaomi_crux/tree/thirteen-plus)
- [PE Recovery](https://github.com/coachpo/crux-pe13-recovery/tree/crux-pe13-functional)
- [Crux 内核](https://github.com/coachpo/kernel_xiaomi_crux/tree/thirteen)

每次发布的确切源码版本与安装兼容条件见对应 Release。
