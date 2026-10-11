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

## 源码

- [设备树与发布打包工具](https://github.com/coachpo/device_xiaomi_crux/tree/thirteen-plus)
- [PE Recovery](https://github.com/coachpo/crux-pe13-recovery/tree/crux-pe13-functional)
- [Crux 内核](https://github.com/coachpo/kernel_xiaomi_crux/tree/thirteen)

每次发布的确切源码版本与安装兼容条件见对应 Release。
