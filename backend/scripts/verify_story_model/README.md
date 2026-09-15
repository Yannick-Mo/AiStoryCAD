# 故事模型改造的验证脚本

`docs/故事模型设计规格.md` 第 12 节要求：改造完成后，第 7 节那八条「必须能回答的问题」
**逐条实际验证一遍**。这六个脚本就是那次验证的可重跑版本——它们不是单元测试，
而是**打真实数据库、走真实工具层**的验收程序（单元测试在 `backend/tests/`）。

| 脚本 | 回答什么 | 会不会写库 |
|---|---|---|
| `check_spec_questions.py` | 规格第 7 节八条问题 + `search` 是否覆盖正文 | **只读** |
| `check_writer_chain.py` | 写手上下文（`build_for_writing`）与 `writer.yaml` 渲染 | **只读**（不调 LLM） |
| `check_framework_render.py` | **每轮提示词里的框架树**（幕索引/全局章号/已写未写/人物关系）+ 缓存失效分区 | **只读** |
| `check_scale.py` | **目标规模**（4 幕 × 30 章 = 120 章）：全局章号、范围读的预算截断、进度断点、框架树预算与耗时 | 只读\* |
| `check_editor_sync.py` | 编辑器增量同步 + 顺序规范（插到谁之后 / 换幕 / 密集编号） | **会写** |
| `check_content_and_setups.py` | 正文写入工具 + 伏笔（含逾期判定） | **会写** |

除 `check_editor_sync` / `check_content_and_setups` 两个**必须**打副本库之外，其余四个都是只读的
（\* `check_scale` 会造一个 120 章的项目来压预算逻辑，但**全程不 commit、最后 rollback**，所以不落库）。
`run_all.sh` 会拦住库名里不带 `test` / `copy` / `migtest` / `verify` 的库。

## 怎么跑

脚本**随镜像一起发布**（`backend/Dockerfile` 里有 `COPY ./scripts ./scripts`），
所以对着跑着的容器就能验，不必挂源码：

```bash
# 只读的那几个（会直接打开发库）
docker compose exec -T backend python /app/scripts/verify_story_model/check_spec_questions.py
docker compose exec -T backend python /app/scripts/verify_story_model/check_scale.py          # 120 章规模
docker compose exec -T backend python /app/scripts/verify_story_model/check_framework_render.py
docker compose exec -T backend python /app/scripts/verify_story_model/check_writer_chain.py

# 全套六项：先照一个副本，再指向副本
docker compose exec -T db psql -U postgres -c "CREATE DATABASE storyforge_verify"
docker compose exec -T db sh -c "pg_dump -U postgres storyforge" \
  | docker compose exec -T db psql -U postgres -d storyforge_verify
bash backend/scripts/verify_story_model/run_all.sh storyforge_verify
```

`run_all.sh` 用的是 `docker compose run -v "$PWD/backend:/app"`——它把工作区**挂**进
容器，所以改完脚本马上生效（`exec` 形式跑的是镜像里那份，要重建才更新）。
`run_all.sh` 会打印每条命令与最终 `FAILURES` 计数，任何一项非零就以非零码退出。

（`*.md` 在 `backend/.dockerignore` 里，所以这份 README 本身不在镜像内。）

```bash
# 只读的两项，打开发库
docker compose exec -T backend python /app/scripts/verify_story_model/check_spec_questions.py

# 全部四项：先照一个副本，再指向副本
docker compose exec -T db psql -U postgres -c "CREATE DATABASE storyforge_verify"
docker compose exec -T db sh -c "pg_dump -U postgres storyforge" \
  | docker compose exec -T db psql -U postgres -d storyforge_verify
bash backend/scripts/verify_story_model/run_all.sh storyforge_verify
```

脚本都在容器里执行（它们 `sys.path.insert(0, "/app")`）。`run_all.sh` 会打印
每条命令与最终 `FAILURES` 计数，任何一项非零就以非零码退出。

## 为什么不用 pytest 覆盖这部分

单元测试跑在 `storyforge_test` 上、由当前模型 `create_all` 建表——它验证的是
「代码符合模型」。这几个脚本验证的是**「真实数据经真实迁移之后，八个业务问题
还答得出来」**，包括迁移本身造成的数据形态（例如迁移过来的章都没有
「本章写成了什么」，`read_progress` 必须把这一点点出来）。两件事不能互相替代：
*验收时这两个脚本真的抓出过两个 bug*——deferred 列在 async 会话里被裸读
（`MissingGreenlet`），以及 `characters.role` 里全是 `protagonist`/`antagonist`
这种契约不认的旧值。
