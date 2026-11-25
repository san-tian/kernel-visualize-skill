# 一轮多 LoRA Decode 的 kernel 对照

从草稿提议到验收，沿九步前向追踪请求甲的第三个验证位置「喝」。沿页面向下读，就能看到每一步做什么、六处改动怎样影响下一步。Q/KV 的显存写入对照、验收的逐词例子和单卡开销都可以直接读。点算子名就能逐项查看 Input、伪代码、Output、CTA 分工与通信。在 GitHub 文件页选择 **Download raw file**，再用浏览器打开保存的 HTML。[打开离线示例文件](index.html)

## 第 3 步怎样省掉 480 KiB 写入？

Q 和 KV 使用同一行输入。在旧实现中，两路各向显存写一张完整的 BF16[24,5120]，每张 240 KiB，随后各自的收缩核再读取有效行。当前实现直接在收缩的线程块里还原 FP8 输入，两路的宽 BF16 临时写入合计降至 0 B。

![Q/KV 投影的两条执行路径及本卡宽 BF16 临时量的写入变化](qkv.png)

点开当前实现的 `_slot_qkv_shrink_deq_2d_kernel`。下面跟踪一个 CTA，为位置「喝」计算 Q 的前 16 个秩分量。

**Input**　`slot[2]=2`，物理权重下标为 1。第一轮取本层量化输入 `X[2,0:256]`（256 B）、覆盖这 256 列的 8 个比例（8 B）和本层 Q 的 `A_Q[1,0:16,0:256]`（8 KiB）。

```text
acc = zeros(16, fp32)
for k in range(0, 5120, 256):
    x = dequant_bf16(X[2, k:k+256], scale_for(2, k:k+256))
    acc += A_Q[1, 0:16, k:k+256] @ x
H_Q[2, 0:16] = bf16(acc)
```

**Output**　这个 CTA 累加 20 轮，向本卡显存写 `H_Q[2,0:16]`，16 个 BF16 共 32 B。展开核稍后会读取它。这个收缩核的跨卡通信量为 0 B。示例里的行号与槽号来自这批请求，5120 取自模型输入宽度，256 和 16 取自该核的分块。

## 同步博客更新

此案例取自 `cp4-compression-and-multi-lora-decode.html` 的 `#decode-comparison`。维护这张博客对照页时，在同一轮修改中从定稿 HTML 更新 `index.html` 和 `qkv.png`。网页里的批量、形状、分块和统计口径也随正文导出。

在 skill 仓库根目录运行下列命令，将第一个参数替换为定稿的博客 HTML 路径。生成 HTML 使用 Python 3，截取预览需要 Playwright、Chromium 和支持中文的字体。

```sh
python3 scripts/export_decode_example.py /path/to/cp4-compression-and-multi-lora-decode.html \
  examples/decode-comparison/index.html --preview examples/decode-comparison/qkv.png
```

打开导出页，直接读完九步流程，再任选一个算子核对其输入、输出和通信。在手机宽度和离线环境下检查改动涉及的交互，将有变化的示例文件同步到本仓库。
