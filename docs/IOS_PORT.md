# 把 Imagechanger 集成进 iOS App(Framy Go)的实施规划

> 给 Claude Code 的任务说明。参考实现在本仓库:`imagechanger/*.py`(Python,有测试)和 `web/patcher.js`、`web/jpeg2heic.js`(JS,与 Python 输出一致,有对比测试)。**Swift 版以它们为准逐模块移植,不要凭印象重写。**

## 0. 目标与原则
- 给旧机型的 HEIC(含 Live Photo)补上「摄影风格调色盘」和 iOS 27 颗粒所需的数据,**像素不重新编码**,存回相册后在「照片 → 编辑」里可用。
- 在 iOS 上我们能直接用 **PhotoKit 拿到原始资源**,所以:
  - 不再有网页里"相册选图被转成 JPEG"的问题;
  - Live Photo 可以连同配对视频原样保留;
  - 不需要 HEVC 编码器(只有"输入真的是 JPEG"的降级路径才需要)。
- **全程不使用 `UIImage`、`PHImageManager.requestImage`、`CGImageDestination` 重写原图**:它们会转码、丢元数据。只读写原始字节。
- 未在真机验证过的环节(见 §8)要在 App 里用开关/说明标出,不要对用户承诺效果。

## 1. 模块划分(对应本仓库文件)
| Swift 模块 | 移植自 | 说明 |
| --- | --- | --- |
| `BMFF.swift` | `imagechanger/bmff.py` | 盒子迭代;`Meta` 模型(items、refs、properties、idat、other);解析 `pitm/iinf(infe v2/3)/iloc(v0-2)/iref(v0/1)/iprp(ipco+ipma 窄/宽)/idat`;序列化 |
| `Assemble.swift` | `assemble.py` | 新 ftyp + 新 meta 在前,原 mdat 等盒子原样保留并平移偏移,新载荷追加到末尾一个新 mdat;meta 大小与偏移值无关,先探测再回填 |
| `ExifEdit.swift` | `exif.py` | TIFF/MakerNote 编辑:插入标签 `0x54`;改写 IFD0 的 Make/Model/Software |
| `StylesPlist.swift` | `styles.py` | 构造 styles 二进制 plist(可用 `PropertyListSerialization`,格式 `.binary`) |
| `Texture.swift` | `texture.py` | 颗粒数据集 |
| `ICC.swift` | `icc.py` | 生成 Display P3 Linear ICC;或直接把 Python 生成的 520 字节作为资源 |
| `Resources/` | `neutral_tile.py`、`matte_tile.py`、`web/constants.js` | 中性 HEVC 瓦片(hvcC+sample)、空遮罩(hvcC+sample)、ICC 的 colr 盒。**由脚本导出成二进制文件**(见 §6),不要手抄十六进制 |
| `Patcher.swift` | `patch.py` | 编排,见 §3 |
| `PhotoLibrary.swift` | 新写 | PhotoKit 读写,见 §4 |
| `Intents.swift` | 新写 | Siri/快捷指令,见 §5 |

只用 Foundation/PhotoKit,不引第三方依赖。

## 2. 数据格式要点(Swift 实现必须一致)
- 文件偏移:iloc 统一写 version 1、4 字节 offset/length、无 base offset、单 extent;`construction_method=1` 表示在 idat 内。原条目的载荷位置不动,只做平移。
- 新增条目的 `infe` 都是 version 2、`hidden` 标志位 = 1。
- 新增的属性一律**追加**到 ipco 末尾(不改已有索引);ipma 条目按下表顺序,"T"= essential。
- 原图若有 `irot`,所有新增图像条目也关联同一个 irot 属性(essential);没有就不加。
- 目标集合 `targets = [primary] + [第一个 tmap 条目(如果有)]`。

### 2.1 补调色盘:新增的条目
| 条目 | 类型 | 属性关联(顺序) | 引用 | 载荷 |
| --- | --- | --- | --- | --- |
| 线性缩略图 | hvc1 | ispe(缩略图的)F、irot T、pixi F、auxC(`tag:apple.com,2023:photo:aux:linearthumbnail`)T、hvcC(缩略图的)T | auxl → targets | **复用照片自带缩略图的字节**(无需编码);该缩略图的 pixi 若不是 3×10bit 则追加其 pixi |
| 增量图瓦片 ×(rows×cols) | hvc1 | ispe(512×512)T、colr(P3 线性 ICC)T、hvcC(中性瓦片)T | — | 中性瓦片 sample(每块一份) |
| 增量图 grid | grid | colr T、ispe(dw×dh)F、irot T、pixi(3×10bit)F、auxC(`…:aux:styledeltamap`)T | auxl → targets;dimg grid → 全部瓦片 | idat 内描述符:`[0,0,rows-1,cols-1] + u16(dw) + u16(dh)` |
| styles | `uri `,name=`metadata`,content_type=`tag:apple.com,2023:photo:metadata:styles` | 无 | cdsc → targets | styles plist(§2.3) |

- `rows = ceil(dh/512)`,`cols = ceil(dw/512)`;pixi 3×10bit = `fullbox pixi, [3,10,10,10]`。
- 增量图尺寸表(按主图**存储**尺寸,横竖互换也适用):`4032×3024 → 2880×2160`;`5712×4284 → 4096×3072`;`3088×2316 → 2240×1680`。表外尺寸(如 48MP)**报错,不要猜**。
- ftyp:在兼容品牌里 `MiHB` 之后插入 `MiHA`、`heix`(没有 MiHB 就追加到末尾)。
- Exif:按 §2.4 处理。

### 2.2 补颗粒(iOS 27):缺一不可,必须成套
- 12 个遮罩条目 hvc1:每个属性 ispe(768×576)F、pixi(1 通道 8bit:`[1,8]`)F、auxC(`tag:apple.com,2026:photo:aux:<名>`)T、hvcC(空遮罩)T、irot T;引用 auxl → targets;载荷=空遮罩 sample(全 0、全范围单色)。
  名称依次:`semanticnosematte, semanticskinmattev2, semanticnonfaceskinmatte, semanticlipsmatte, semanticteethmattev2, semanticpersonmatte, semanticglassesmattev2, semanticeyebrowsmatte, semantictattoomatte, semantichandsmatte, semanticearsmatte, semanticfaceskinmatte`。照片里已存在的同 URI 遮罩跳过。
- 12 个 XMP 旁车:`mime` 条目,content_type=`application/rdf+xml`,cdsc → 对应遮罩;载荷见 `texture.py` 的 `XMP`(逐字节一致,Python/JS 已共用)。
- `texture_styles`:`uri ` 条目,name=`metadata`,content_type=`tag:apple.com,2026:photo:metadata:texture_styles`,cdsc → targets;载荷是 binary plist:`Preset=Standard, CaptureType=LF, CaptureMode=Still, PortType=PortTypeBack, HardwareModel=iPhone19,2, TextureStylePeopleDataVersion=3, FilmGrainSeed=<0…255>`。
- `FilmGrainSeed = CRC32(第一块主图瓦片字节) % 256`;主图不是 grid 时用主图条目字节。(`seed_source` 见 patch.py)
- 只有遮罩没有 texture_styles 无害;**只有 texture_styles 没有遮罩会让整个调色盘消失**。
- 已有 styles 但没有 texture_styles 的照片(iPhone 16/17 原生、或之前只补过调色盘):只做 §2.2,不碰其余部分。

### 2.3 styles plist(键均为字符串)
`"0"=16`(schema)、`"1"`=51840 字节(864 格×10 项×RGB,float16 小端)恒等变换:每格 `[0,0,0, 1,0,0, 0,1,0, 0,0,1, 其余 0]`、`"2"=true`、`"3"`=516 字节(头 `01 01 00 00` + 256 个 u16 直线)、`"4"=5.384615421295166`、`"5"=0`、`"6"`=统计字典(见 `styles.py`)、`"7"={PeopleRatio:0,SkinRatio:0,PersonMasksValidHint: 有人像遮罩?1:-1}`、`"c"`/`"d"`=32×32 float16 平坦图(0.3115234375 / 0.200927734375)、`"e"=32,"f"=32,"g"=0x4C303068,"h"=Gain/4,"i"={OriginalRangeMin,OriginalRangeMax,Gain=14.565662384033203},"j"=1.0,"k"=false,"l"=false`。
浮点值必须编码为 real(不是整数)。以 `styles.py` 为准;测试里按 `plistlib.loads` 语义比较。

### 2.4 Exif / MakerNote
- IFD0 的 Make=`Apple`、Model=`iPhone 18 Pro`、Software=`27.0`(只改已有标签,不新增)。
- MakerNote(`Apple iOS\0` 头,偏移相对 MakerNote 起点)插入标签 `0x54`,type 7(undefined),载荷是 binary plist:`{"0":1,"1":0.0,"2":0.0,"3":1.0,"4":1,"5":1,"6":4,"7":0}`(键为字符串,"1""2""3" 是 real)。做法:重建 MakerNote IFD(按 tag 排序、已有 out-of-line 偏移整体 +12)、把新 MakerNote 追加到 TIFF 末尾、改写 0x927C 的长度和偏移;旧 MakerNote 留在原处不用。
- Live Photo 的 MakerNote 里有配对用的 ContentIdentifier(标签 0x11),**必须原样保留**——它是 HEIC 与 MOV 重新配对的依据。

## 3. `Patcher` 接口
```swift
struct PatchOptions { var grain = true; var asIPhone18Pro = true; var deltaSizeOverride: CGSize? }
enum PatchError: Error { case notHEIC, alreadyStyled, noThumbnail, noExif, unsupportedSize(w:Int,h:Int), corrupt(String) }
func patch(_ heic: Data, options: PatchOptions = .init()) throws -> (data: Data, report: PatchReport)
```
- 纯函数、可在后台线程运行、不触碰相册。
- 需要的前置:主图有 ispe;存在 thmb 缩略图(带 hvcC/ispe);存在 Exif 条目(含 Apple MakerNote)。缺少则抛对应错误,**不要静默降级**。
- 单测:用仓库里 `tests/fixture.py` 生成的合成 HEIC 和 Python 输出做对比(§6)。

## 4. 相册读写(PhotoKit)
读(保证拿到原始字节):
```swift
let res = PHAssetResource.assetResources(for: asset)
// 静态图:type == .photo(或 .fullSizePhoto 不要用,那是编辑后的)
// Live Photo 视频:type == .pairedVideo
let opts = PHAssetResourceRequestOptions(); opts.isNetworkAccessAllowed = true  // iCloud 原图
PHAssetResourceManager.default().writeData(for: r, toFile: url, options: opts) { err in ... }
```
- 选择原图资源用 `.photo`,不是 `.fullSizePhoto`(后者是用户编辑后的版本)。
- 选取:`PHPickerViewController`,`PHPickerConfiguration(photoLibrary: .shared())` 才有 `assetIdentifier`;再用 `PHAsset.fetchAssets(withLocalIdentifiers:)`。支持多选、含 Live Photo(`filter: .any(of: [.images, .livePhotos])`)。
- 判断 HEIC:读文件头 `ftyp` 品牌,不要信 `uniformTypeIdentifier` 以外的东西;`public.jpeg` 走 §7。

写回(新资产,不覆盖原图):
```swift
PHPhotoLibrary.shared().performChanges {
    let req = PHAssetCreationRequest.forAsset()
    let o = PHAssetResourceCreationOptions(); o.shouldMoveFile = true; o.uniformTypeIdentifier = "public.heic"
    req.addResource(with: .photo, fileURL: patchedHEIC, options: o)
    if let mov = pairedVideoURL { let v = PHAssetResourceCreationOptions(); v.shouldMoveFile = true
        req.addResource(with: .pairedVideo, fileURL: mov, options: v) }
}
```
- 保持原拍摄时间/位置:`req.creationDate`、`req.location` 从原 asset 复制。
- Info.plist:`NSPhotoLibraryUsageDescription`、`NSPhotoLibraryAddUsageDescription`。
- **不要删除原照片**;让用户自己决定。结果页提供「存入相册」「分享/存到文件」两个出口。
- 不要走 `UIImageWriteToSavedPhotosAlbum` / `PHAssetChangeRequest.creationRequestForAsset(from: UIImage)`。

## 5. Siri / 快捷指令 / 分享
- **App Intents**(iOS 16+):`StyleLastPhotoIntent`(处理相册最新一张)、`StylePhotosIntent(photos: [IntentFile])`(从快捷指令/分享菜单接收文件,返回处理后的 `IntentFile`)。提供 `AppShortcutsProvider`,短语如「用 Framy Go 加调色盘」。
- **Share Extension**:接收 `public.heic`;扩展内内存吃紧,直接 `Data` 处理 12MP/24MP 约几十 MB,逐张串行。
- 注意:来自分享菜单/快捷指令的文件可能已被系统换成 JPEG,`patch` 会抛 `notHEIC`,给出清楚提示。

## 6. 资源导出与测试
- 加脚本 `tools/export_ios_resources.py`:把 `neutral_tile.HVCC/SAMPLE`、`matte_tile.HVCC/SAMPLE`、`icc.colr_box()`、`texture.XMP` 写成二进制文件到 `ios/Resources/`。常量更新后重跑即可,CI 里加"资源与 Python 一致"的检查。
- 对比测试:Python 对同一合成 HEIC 的输出作为金标准;Swift 输出需满足(与 `tests/test_js_parity.py` 同标准):条目 ID/类型/名称/hidden/属性关联一致;瓦片、缩略图、属性盒逐字节相同;plist 按语义相同;MakerNote 标签集合相同;解码像素不变(可用 ImageIO 解码前后对比)。
- 其余用 XCTest:偏移合法(所有 extent 在文件内)、重复处理抛 `alreadyStyled`、仅补颗粒路径。

## 7. 降级:输入真的是 JPEG
- 只在用户明确选择时才启用(有损、会缩小体积差异、丢 HDR)。
- 参考 `web/jpeg2heic.js`:解析 JPEG 的 Exif/ICC/方向,用 **VideoToolbox(VTCompressionSession, HEVC)** 或 AVAssetImageGenerator 之外的路径编码主图和 1024 长边的缩略图,取 hvcC 与 sample 拼成 HEIC 再走 `patch`。
- 原则:**保持像素与朝向**——只按 EXIF 方向写 irot(1→无,3→2,6→3,8→1),不要转像素;若系统已经转正了像素,则方向置 1,避免转两遍。缩略图保持原图比例。
- 色彩:有 P3 ICC 则 colr nclx 用 primaries=12;否则 sRGB。

## 8. 未经真机验证的点(App 里需要日志/开关并回报)
1. 补调色盘:已在 iOS 27 真机上确认**调色盘出现**(网页版输出)。
2. **颗粒/质感:到目前仍未出现**——原因未明(可能是设备/系统限制,或某个字段值与原生不同)。App 里默认开启但不要承诺;提供「导出诊断」(处理前后的 `inspect` JSON)方便对比原生 iPhone 18 Pro 文件。
3. PhotoKit `addResource(.photo)` 保存时系统是否保留我们新增的 items:**必须真机验证**(存回后重新读取 `.photo` 资源,确认仍含 styles 条目)。若被剥离,备选:存到「文件」让用户手动导入。
4. Live Photo 回写后是否仍能配对、且调色盘仍在。
5. 12MP/24MP/前置之外的尺寸(含 48MP)暂不支持。

## 9. 交付顺序(建议里程碑)
1. `BMFF` + `Assemble` + 往返测试(解析→序列化→再解析,条目不变)。
2. `ExifEdit`、`StylesPlist`、`ICC`、资源导出。
3. `Patcher`(调色盘)+ 与 Python 金标准对比通过。
4. 加颗粒、仅补颗粒路径、Exif 机型。
5. `PhotoLibrary`:选取 → 读原图 → 处理 → 写回(含 Live Photo);真机验证 §8。
6. App Intents / Share Extension。
7. JPEG 降级路径(可选)。

## 10. 不要做的事
- 不要改原图像素,不要重新编码主图或缩略图。
- 不要用 UIImage/CoreImage 路径读写原图。
- 不要在代码里手抄长十六进制常量;一律由脚本导出。
- 不要把用户照片上传到任何服务器(与网页版一致:全部本地)。
