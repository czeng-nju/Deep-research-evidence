# 手动上传 GitHub

目标账号：`czeng-nju`；建议仓库名：`deep-research-evidence`；可见性：**Public**。本文件夹已准备好材料，尚未创建或上传仓库。

## 推荐：网页上传

1. 登录 GitHub，打开 https://github.com/new 。Owner 选择 `czeng-nju`，Repository name 填 `deep-research-evidence`，选择 Public。Description 可填：`Evidence-traceable deep research skill with scoped claims, resumable records, and reproducible checks.`
2. 此文件夹已包含 README 和 .gitignore，不必另生成 README、.gitignore 或许可证。点击 **Create repository**。若同名仓库已存在，先确认它就是你要使用的仓库，再进入该仓库。
3. 空仓库中点击 **uploading an existing file**；已有仓库可用 **Add file → Upload files**。
4. 将**本文件夹里面的文件和子文件夹**拖入上传区，保留 skill、docs、examples、tests、evaluation、evidence、scripts、dist 的层级。仓库根目录应直接出现 README.md；不要把整个外层发布文件夹作为额外一层上传，也不要只上传压缩包。
5. Commit message 填 `Publish deep-research-evidence v0.3.0`，点击 **Commit changes**。
6. 检查 README 正常显示，skill/SKILL.md、docs/USAGE.md、examples/minimal/README.md 及 dist 中的 .skill 能打开或下载。预期仓库地址为 https://github.com/czeng-nju/deep-research-evidence ，实际以创建结果为准。

GitHub 网页一次最多上传 100 个文件，单文件上限 25 MiB；本发布包已检查在限额内。macOS 的 .gitignore 是隐藏文件；如拖拽时未包含，可按 Command+Shift+. 显示隐藏文件后补传，或使用下面的 Git 方式。示例运行后的 run/ 不应上传。

## 可选：使用本机 Git

先在 GitHub 创建上面的空仓库，再在本发布文件夹打开终端执行：

```sh
git init -b main
git add .
git commit -m "Publish deep-research-evidence v0.3.0"
git remote add origin https://github.com/czeng-nju/deep-research-evidence.git
git push -u origin main
```

若 Git 提示未配置作者，在本仓库设置自己的 user.name 和 user.email 后重新 commit。认证按 GitHub 登录提示或已有凭据配置完成，不需要把密码或 token 发给任何人。已有 Git 仓库时不要重复 init 或覆盖现有 remote，先检查当前配置。

## 平台使用入口

GitHub 上传的是完整项目；让 Agent 加载 `skill/SKILL.md`。若平台要求上传 Skill 归档，使用 `dist/deep-research-evidence-0.3.0.skill`，不要将整个项目或 evaluation 目录作为 Skill 包上传。实际运行必须读取平台给定案例，不复制示例或历史评测答案。

官方说明：[创建仓库](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)、[上传文件](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository)。
