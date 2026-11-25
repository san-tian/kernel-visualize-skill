# 一轮多 LoRA Decode 的 kernel 对照

沿九步总览从上往下读，可以跟着同一批请求从草稿提议走到 token 提交。遇到两条路径时，先看这一步的共同输入，再横向比较左边的旧实现和右边的当前实现，最后看两边交给下一步的共同输出。草稿提议、因果注意力和验收各画一次，其余六步可以逐一对照。

**完整示例**包含从草稿提议到验收的九个阶段、六处 kernel 改动，以及可以切换 GPU 的计算量、显存和通信明细。在 GitHub 文件页点击 **Download raw file**，用浏览器打开保存的 HTML 即可离线交互。[打开完整示例文件](index.html)

## 专家槽号表为何写了 20 遍

旧刷新函数按登记清单逐项取出专家槽号表，遇到一项就复制一次。历史报告记录了 20 项指向同一块显存。新版先按表的显存起始地址去重，这份清单只需写一次。第 2 步还给出了另一项核对：用固定版本的旧登记逻辑在 CPU 上重复提交同一个对象 20 次，清单只留下 1 项。报告中的 20 次复制有记录，那一批 20 项是怎样登记进来的仍缺逐项记录。示例把这两件事分别画在清单和刷新过程里。

## Q/KV 投影

下图截自九步总览的第 3 步。两边拿到同一份 FP8 输入 X。旧实现为 Q 和 KV 各写一份 240 KiB 的 BF16 输入，当前实现在块内还原并做乘法，省下两次写回。两路输出的短向量仍各写 1.5 KiB。打开完整 HTML 的「Q/KV 投影」详图，可以逐行查看每个 kernel 读写哪些张量、发射多少个 CTA。

![九步总览中的 Q/KV 投影，从共同输入对照两条读写路径，宽 BF16 临时量的写入从 480 KiB 降为 0 B](qkv.png)

## 同步博客更新

此案例取自 `cp4-compression-and-multi-lora-decode.html` 的 `#decode-comparison`。维护这张博客对照页时，在同一轮修改中从定稿 HTML 更新 `index.html` 和 `qkv.png`。网页里的批量、形状、分块和统计口径也随正文导出。

在 skill 仓库根目录运行下列命令，将第一个参数替换为定稿的博客 HTML 路径。生成 HTML 使用 Python 3，截取预览需要 Playwright、Chromium 和支持中文的字体。

```sh
python3 scripts/export_decode_example.py /path/to/cp4-compression-and-multi-lora-decode.html \
  examples/decode-comparison/index.html --preview examples/decode-comparison/qkv.png
```

打开导出页，与博客核对本次改动所在的行、两侧张量读写和 CTA 口径。若改动涉及 GPU 选择或槽号回放，在离线页操作一次。将有变化的示例文件随本次博客更新同步到本仓库。
