# 补充课：如何从Python导入名找到项目文件

## 你遇到的问题

项目很多文件包含：

```python
from quant_lab.data.schema import require_columns
```

但在项目根目录直接寻找 `quant_lab/data/schema.py` 时找不到。

原因不是文件丢失，而是本项目采用Python常见的 `src` 目录布局。

## 一、真实文件在哪里

`schema.py` 的项目相对路径是：

```text
src/quant_lab/data/schema.py
```

本机完整路径是：

```text
C:\Users\lenovo\Desktop\水滴石穿\a-share-factor-lab\src\quant_lab\data\schema.py
```

目录结构：

```text
a-share-factor-lab
├─ pyproject.toml
├─ scripts
├─ tests
└─ src
   └─ quant_lab
      ├─ data
      │  ├─ __init__.py
      │  └─ schema.py
      ├─ factors
      ├─ evaluation
      └─ backtest
```

## 二、为什么导入语句里没有 `src`

`src` 是“源代码根目录”，不是Python包名的一部分。

项目的 `pyproject.toml` 写着：

```toml
[tool.setuptools.packages.find]
where = ["src"]
```

它告诉安装工具：从 `src` 目录下面寻找Python包。因此：

| Python导入名 | 实际文件 |
|---|---|
| `quant_lab.data.schema` | `src/quant_lab/data/schema.py` |
| `quant_lab.factors.library` | `src/quant_lab/factors/library.py` |
| `quant_lab.evaluation.preprocess` | `src/quant_lab/evaluation/preprocess.py` |
| `quant_lab.backtest.engine` | `src/quant_lab/backtest/engine.py` |

导入路径中的点号可以先理解为子目录分隔符，然后在最前面补上 `src/`，最后一个名称加 `.py`：

```text
quant_lab.data.schema
↓ 点号换成斜杠
quant_lab/data/schema
↓ 最前面补src，最后补.py
src/quant_lab/data/schema.py
```

## 三、如何让Python自己告诉你文件位置

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -c "import quant_lab.data.schema as s; print(s.__file__)"
```

本项目实际验证得到的文件就是：

```text
...\a-share-factor-lab\src\quant_lab\data\schema.py
```

还可以定位函数定义行：

```powershell
.\.venv\Scripts\python.exe -c "import inspect; from quant_lab.data.schema import require_columns; print(inspect.getsourcefile(require_columns)); print(inspect.getsourcelines(require_columns)[1])"
```

当前 `require_columns()` 定义在该文件第89行左右。

## 四、`schema.py` 负责什么

该文件不是外部依赖，也不是运行时自动生成文件。它是本项目的数据契约代码，主要包含：

1. 主键、价格、必需列和状态列常量；
2. `normalize_daily_frame()`：统一日期、代码、数值和布尔类型；
3. `validate_daily_frame()`：检查重复主键、非法价格、负成交量等；
4. `require_columns()`：检查某个函数收到的数据是否具备所需字段。

## 五、`require_columns()` 为什么到处使用

函数本身很短，逻辑相当于：

```python
def require_columns(frame, columns):
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing columns: {missing}")
```

例如动量函数需要：

```python
require_columns(frame, ["symbol", "close"])
```

如果输入表没有 `close`，程序会立即给出：

```text
ValueError: Missing columns: ['close']
```

这叫“尽早失败”：在函数入口就说明输入不符合要求，而不是运行到后面才出现难懂的索引错误，或更糟糕地生成错误结果。

## 六、为什么不应该把文件复制到根目录

不要再创建一份：

```text
quant_lab/data/schema.py
```

否则项目会出现两份同名模块：

- 一份在 `src/quant_lab/data/schema.py`；
- 一份在根目录 `quant_lab/data/schema.py`。

不同运行方式可能导入不同副本，修改一份却运行另一份，问题会更难定位。保留一个标准来源才正确。

## 七、编辑器里怎样找

从项目根目录依次展开：

```text
src → quant_lab → data → schema.py
```

也可以在编辑器使用“转到定义”：把光标放在 `require_columns` 上，按常用的转到定义快捷键。若编辑器使用的解释器是项目 `.venv`，通常会直接跳到正确文件。

## 八、检查编辑器解释器

编辑器应使用：

```text
C:\Users\lenovo\Desktop\水滴石穿\a-share-factor-lab\.venv\Scripts\python.exe
```

若选到系统Python或旧项目虚拟环境，转到定义可能失败，甚至跳到旧目录。

## 九、练习

请亲自找到下面三个文件：

1. `quant_lab.factors.library`；
2. `quant_lab.evaluation.diagnostics`；
3. `quant_lab.portfolio.weights`。

答案：

```text
src/quant_lab/factors/library.py
src/quant_lab/evaluation/diagnostics.py
src/quant_lab/portfolio/weights.py
```

## 十、最低过关标准

看到：

```python
from quant_lab.some_folder.some_module import something
```

你能先去下面寻找：

```text
src/quant_lab/some_folder/some_module.py
```

并能用模块的 `__file__` 属性让Python打印真实导入位置。

