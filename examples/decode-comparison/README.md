# 一轮多 LoRA Decode 的 kernel 对照

原博客第 7 页讲优化前，第 8 页讲优化后。这个案例把同一批请求的两条执行路径放进一张图。向下读是一轮 Decode 的顺序，横向读是同一个动作在优化前后怎样实现。灰色通栏标出两版共用的步骤，右边绿色标出当前实现。

**完整示例**包含从草稿提议到验收的九个阶段、六处 kernel 改动，以及可以切换 GPU 的计算量、显存和通信明细。在 GitHub 文件页点击 **Download raw file**，用浏览器打开保存的 HTML 即可离线交互。[打开完整示例文件](index.html)

## Q/KV 投影

从图里「把 X 的 FP8 数值按分组 scale 还原」这一行开始。左栏的去量化 kernel 把宽 BF16 张量写到显存，下一个收缩 kernel 再读它。右栏把还原和收缩放进同一次发射，块内拿到的数直接参与点积。再往下一行看，Q 和 KV 各自收缩出的短向量仍要写到显存，供展开 kernel 使用。图中的字节数按本例张量推算，CTA 数按本例发射网格计算。

![同一批输入的 Q/KV 对照图，逐行展示去量化、收缩、展开的读写位置与发射数](qkv.png)

## 同步博客更新

此案例取自 `cp4-compression-and-multi-lora-decode.html` 的 `#decode-comparison`。维护这张博客对照页时，在同一轮修改中从定稿 HTML 更新 `index.html` 和 `qkv.png`。网页里的批量、形状、分块和统计口径也随正文导出。

在 skill 仓库根目录运行下列命令，将第一个参数替换为定稿的博客 HTML 路径。生成 HTML 使用 Python 3，截取预览需要 Playwright、Chromium 和支持中文的字体。

```sh
python3 scripts/export_decode_example.py /path/to/cp4-compression-and-multi-lora-decode.html \
  examples/decode-comparison/index.html --preview examples/decode-comparison/qkv.png
```

打开导出页，与博客核对本次改动所在的行、两侧张量读写和 CTA 口径。若改动涉及 GPU 选择或槽号回放，在离线页操作一次。将有变化的示例文件随本次博客更新同步到本仓库。
