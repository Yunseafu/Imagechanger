# Imagechanger

给 iPhone 16 以前机型拍的 HEIC 照片补上「摄影风格(Photographic Styles)」所需的数据,让"照片"App 的编辑页出现风格调色板。**像素不变**,只增补元数据与条目。

> ⚠️ **实验性,未经真机验证。** 本仓库的测试只能证明:输出文件结构合法、像素逐字节一致、原有条目与 Exif 不变。
> "调色板是否真的出现、能否调节"必须在你自己的 iOS 设备上确认(见下方「验证」)。务必保留原图。
> 非 Apple 官方项目。

## 原理(自己实现,未使用他人代码)

相册根据**文件里有什么数据**决定提供哪些编辑控件,而不是看机型。iPhone 16+ 的原生风格照片比旧照片多出:

| 数据 | 作用 |
| --- | --- |
| Exif MakerNote 标签 `0x54` | 触发风格调色板 |
| `styles` 元数据条目(二进制 plist,标识 `tag:apple.com,2023:photo:metadata:styles`) | 风格参数:恒等色彩多项式、色调曲线、32×32 光照图、场景统计等 |
| 线性缩略图(辅助图像 `…:aux:linearthumbnail`) | 渲染器唯一的空间变化输入;这里复用照片自带缩略图 |
| 风格增量图(`…:aux:styledeltamap`,512×512 Main10 瓦片组成的 grid) | 全部填中性瓦片 = 无额外修正 |
| `ftyp` 品牌 `MiHA`、`heix` | 格式声明 |

`imagechanger/` 各模块:`bmff.py`(HEIF 容器读写)、`exif.py`(MakerNote 标签插入)、`styles.py`(plist 构造)、`icc.py`(生成 Display P3 Linear 色彩描述)、`neutral_tile.py`(由 `tools/gen_neutral_tile.py` 用 ffmpeg 生成的中性 HEVC 瓦片)、`patch.py`(编排)、`server.py`(局域网服务)。

**范围**:只做调色板(Style)。未实现 iOS 27 质感/颗粒、柔肤、人像图层;同样不会依据画面计算光照图(使用中性默认值)。已知尺寸表仅含 12MP(4032×3024)、24MP(5712×4284)、前置(3088×2316)及其竖向存储;其他尺寸用 `--delta-size WxH` 手动指定。

## 使用

```bash
python -m imagechanger patch IMG_0001.HEIC            # -> IMG_0001_styled.heic
python -m imagechanger inspect IMG_0001.HEIC          # 查看条目图
python -m imagechanger serve --port 8765              # 局域网服务(给 Siri/快捷指令用)
python -m unittest discover -s tests -t .             # 测试(需 pip install pillow pillow-heif)
```

## 网页版(GitHub Pages)

`web/` 是纯静态页面,在浏览器里直接处理照片、不上传(`web/patcher.js` 是 Python 版的 JS 移植,`tests/test_js_parity.py` 保证两者输出一致)。
启用:仓库 Settings → Pages → Source 选 **GitHub Actions**,合并到 `main` 后由 `.github/workflows/pages.yml` 自动部署,地址通常是 `https://<用户名>.github.io/imagechanger/`。
本地预览:`cd web && python3 -m http.server`。Python 常量更新后运行 `python tools/gen_web_constants.py` 重新生成 `web/constants.js`。

## 本地部署

- 直接运行:`pip install .` 后执行 `imagechanger serve`(仅标准库,无其他依赖)。
- Docker:`docker compose up -d --build`,快捷指令里填 `http://<服务器局域网IP>:8765/patch`。
- Siri / 自动化:见 [docs/SIRI.md](docs/SIRI.md)。

服务器**没有登录**,只会响应私有网络地址的客户端;请勿暴露到公网,也不要使用别人搭的服务器(会收到你的原图和 GPS)。

## 验证(请在真机做)

1. 用本工具处理一张旧机型 HEIC,通过 AirDrop/文件 App 传到 iPhone(不要经相册"导入"以外会重编码的途径)。
2. 照片 App → 编辑 → 看是否出现「风格」调色板,拖动色调/色彩是否有可见效果,保存后重新打开仍可调。
3. 若不成功,请提 issue 并附 `inspect` 输出;对比一张原生 iPhone 16/17 照片的 `inspect` 结果即可定位差异。

## 致谢

了解该机制主要参考了社区公开的研究文档(如 nathanatgit/Shalielie 的说明)。本仓库代码为独立重写,常量(ICC、HEVC 瓦片)均由本仓库脚本生成。
