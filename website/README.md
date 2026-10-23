# Codalith website

项目网站位于本目录，使用 Astro + Starlight 输出静态 HTML。英文与简体中文均包含首页、首次查询教程，以及安装、配置、MCP API、运维、架构和评测手册。本地搜索由构建时生成的 Pagefind 索引提供，不需要远程搜索服务。

首页与文档页共用语言图标菜单、明暗主题开关和 GitHub 图标链接。首次访问跟随系统主题；手动选择后保存在浏览器的 `starlight-theme` 偏好中，并在页面和标签页之间同步。

## 本地开发与构建

需要 Node.js 24.18.0（见 `.node-version`）和 Bun 1.4.3。依赖版本固定在 `package.json` 与 `bun.lock` 中。

从仓库根目录运行：

```bash
bun install --cwd website --frozen-lockfile
bun run --cwd website check
bun run --cwd website build
bun run --cwd website preview
```

默认预览地址为 `http://127.0.0.1:4321/Codalith/`。端口被占用时，以终端显示的实际地址为准。修改站点时使用 `bun run --cwd website dev`。搜索需要先运行生产构建，再通过 `preview` 检查。

生产输出为 `website/dist/`，可以由静态服务器直接托管，不依赖 Python MCP 服务。服务器必须将此输出挂载在 `/Codalith/`；直接加载 `/Codalith/guides/mcp-api/`、`/Codalith/zh-cn/start/` 等深层路径应返回对应的 `index.html`。

## 内容和品牌资源

- `src/components/Home.astro` 与 `src/styles/home.css`：两种语言共用的首页与样式。
- `src/content/docs/start.md`、`src/content/docs/zh-cn/`：首次查询教程与中文手册。
- `../docs/*.md`：六份英文手册的唯一正文来源。`scripts/sync-docs.mjs` 在开发、检查、构建前生成英文站点页面并转换链接；不要直接编辑生成的 `src/content/docs/guides/`。
- `../docs/assets/brand/`：通过内置 imagegen 生成的透明 Logo 和图标，包含完整提示词与使用规范。`scripts/sync-brand.mjs` 复制用于站点的资产，不生成或重绘图像。
- `src/evidence/sample.json`：首页展示的公开 sample 语料检索记录。它不是实时 endpoint 或语义质量 benchmark。首页构建会核对示例源码的 SHA-256，源码变化后应重新运行示例并更新证据。

英文正文更新后，需要同步检查中文对应页；它们保留可编辑的独立译文。修改版本、配置或工具契约时，同时核查根目录两份 README、首次查询教程和首页。生成文件、依赖、搜索索引和构建输出均不提交。

## GitHub Pages 发布

当前仓库默认分支为 `master`；检查时 GitHub 返回 `has_pages=false`，尚未启用 Pages。`astro.config.mjs` 为计划使用的标准项目地址设置了 `site: https://cyber-tao.github.io` 和 `base: /Codalith`，不代表该地址已经上线。若启用自定义域名或继承账号域名，先核查实际 origin/base，再修改对应配置并重新验证全部路径。

已获确认的工作流位于 [`.github/workflows/website.yml`](../.github/workflows/website.yml)，本地配置已就绪，尚未推送启用。工作流按 `master` 推送和手动运行发布，PR 只构建检查；构建使用冻结锁文件，发布权限只授予独立的 deploy job。

启用时还需要：

1. 在 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。
2. 确认仓库允许工作流里的 Actions，`github-pages` environment 允许 `master` 部署。
3. 按单独授权的流程提交并推送网站与工作流，或合并包含它们的变更；本地 commit 不触发 Actions。
4. 等待工作流成功，检查首页、一个深层手册页面、中文页面及生产搜索后，才能报告公开发布完成。

工作流不申请管理员 token，不自动启用 Pages，不修改 DNS。它只上传 `website/dist/` 静态输出，不发布源码、索引数据或本地报告。

参考：[Astro 的 GitHub Pages 指南](https://docs.astro.build/en/guides/deploy/github/)和 [GitHub 自定义 Pages 工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。
