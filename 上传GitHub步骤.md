# 上传到 GitHub 组织的操作步骤

> 目标：把 `paper-reading-agent/` 这个目录，作为一个新仓库推到
> `https://github.com/orgs/BNU-RenbenAI-PBL` 组织下。

## 前置
1. 邮箱里接受 GitHub 邀请，加入 `BNU-RenbenAI-PBL` 组织（点 Accept invitation）。
2. 本机已装 Git。

## 步骤（在终端执行，把「你的名字」换成你的 GitHub 用户名）

### 方案 A：先在网页建仓库，再推代码（推荐）
1. 浏览器打开 https://github.com/orgs/BNU-RenbenAI-PBL/repositories
   → 点绿色 **New repository** → 填名字，例如 `你的名字-Paper-Reading-Agent`
   → 选 **Public**（组织仓库通常 public）→ 不要勾选自动生成 README → **Create**。
2. 回到终端，进入项目目录并推上去：
```bash
cd /Users/wennie/WorkBuddy/2026-11-24-16-07-38/paper-reading-agent
git init
git add .
git commit -m "论文阅读陪伴 Agent:后端 + 前端 + 部署配置"
git branch -M main
git remote add origin https://github.com/BNU-RenbenAI-PBL/你的名字-Paper-Reading-Agent.git
git push -u origin main
```

### 方案 B：直接用 gh CLI（如果装了 gh 且已登录）
```bash
cd /Users/wennie/WorkBuddy/2026-11-24-16-07-38/paper-reading-agent
gh repo create BNU-RenbenAI-PBL/你的名字-Paper-Reading-Agent --public --source=. --push
```

## 推上去后
- 检查仓库里**没有 `.env`**（`.gitignore` 已拦）。
- 前端上线 GitHub Pages：仓库 Settings → Pages → Source 选 `main` 分支（或 `gh-pages`），
  拿到 `https://bnurenbenaipbl.github.io/...` 或 `https://用户名.github.io/...` 的 URL。
- 后端上线 Railway（见 README「上线」）。

## 注意
- 组织仓库的 owner 是组织，URL 里的 owner 段是 `BNU-RenbenAI-PBL`，不是你的用户名。
- 推代码前先在本地 `grep -rniE "sk-[a-z0-9]{16}"` 确认前端/全仓库无 key。
