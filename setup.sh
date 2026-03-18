#!/bin/bash
# wechat-radar 一键安装配置脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================="
echo "  wechat-radar 安装配置向导"
echo "=================================="
echo ""

# ── Step 1: Python 环境 ──
echo "[1/5] 检查 Python 环境..."
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.9+"
    echo "   macOS: brew install python3"
    echo "   Ubuntu: sudo apt install python3 python3-venv"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "   Python $PYTHON_VERSION ✓"

# ── Step 2: 虚拟环境 + 依赖 ──
echo ""
echo "[2/5] 安装依赖..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "   虚拟环境已创建"
fi
source .venv/bin/activate
pip install -q -r requirements.txt
echo "   依赖安装完成 ✓"

# ── Step 3: 配置 .env ──
echo ""
echo "[3/5] 配置 AI 模型和推送渠道"

if [ -f ".env" ]; then
    echo "   检测到已有 .env 文件"
    read -p "   是否重新配置？(y/N): " RECONFIG
    if [ "$RECONFIG" != "y" ] && [ "$RECONFIG" != "Y" ]; then
        echo "   跳过配置 ✓"
        SKIP_ENV=true
    fi
fi

if [ "$SKIP_ENV" != "true" ]; then
    cp .env.example .env

    echo ""
    echo "   ── AI 模型配置 ──"
    echo "   推荐选项："
    echo "   1) DeepSeek（国内推荐，便宜快速）"
    echo "   2) OpenAI（GPT-4o-mini）"
    echo "   3) 通义千问"
    echo "   4) 硅基流动"
    echo "   5) 其他（稍后手动编辑 .env）"
    echo ""
    read -p "   选择 AI 模型 [1-5]（默认 1）: " AI_CHOICE
    AI_CHOICE=${AI_CHOICE:-1}

    case $AI_CHOICE in
        1)
            AI_BASE_URL="https://api.deepseek.com"
            AI_MODEL="deepseek-chat"
            PROVIDER_NAME="DeepSeek"
            ;;
        2)
            AI_BASE_URL="https://api.openai.com/v1"
            AI_MODEL="gpt-4o-mini"
            PROVIDER_NAME="OpenAI"
            ;;
        3)
            AI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
            AI_MODEL="qwen-plus"
            PROVIDER_NAME="通义千问"
            ;;
        4)
            AI_BASE_URL="https://api.siliconflow.cn/v1"
            AI_MODEL="Qwen/Qwen2.5-72B-Instruct"
            PROVIDER_NAME="硅基流动"
            ;;
        5)
            echo "   请稍后手动编辑 .env 文件"
            AI_BASE_URL=""
            AI_MODEL=""
            PROVIDER_NAME=""
            ;;
    esac

    if [ -n "$AI_BASE_URL" ]; then
        read -p "   请输入 ${PROVIDER_NAME} 的 API Key: " AI_KEY
        if [ -n "$AI_KEY" ]; then
            sed -i.bak "s|AI_API_KEY=your-key-here|AI_API_KEY=$AI_KEY|" .env
            sed -i.bak "s|AI_BASE_URL=https://api.openai.com/v1|AI_BASE_URL=$AI_BASE_URL|" .env
            sed -i.bak "s|AI_MODEL=gpt-4o-mini|AI_MODEL=$AI_MODEL|" .env
            rm -f .env.bak
            echo "   ${PROVIDER_NAME} 配置完成 ✓"
        fi
    fi

    echo ""
    echo "   ── 推送渠道配置 ──"
    echo "   选择一个推送渠道（可稍后在 .env 中添加更多）："
    echo "   1) 飞书机器人"
    echo "   2) 邮件"
    echo "   3) 钉钉机器人"
    echo "   4) 企业微信机器人"
    echo "   5) Telegram Bot"
    echo "   6) 稍后手动配置"
    echo ""
    read -p "   选择推送渠道 [1-6]（默认 1）: " PUSH_CHOICE
    PUSH_CHOICE=${PUSH_CHOICE:-1}

    case $PUSH_CHOICE in
        1)
            read -p "   请输入飞书 Webhook URL: " FEISHU_URL
            if [ -n "$FEISHU_URL" ]; then
                echo "" >> .env
                echo "FEISHU_WEBHOOK=$FEISHU_URL" >> .env
                echo "   飞书配置完成 ✓"
            fi
            ;;
        2)
            read -p "   发件邮箱地址: " EMAIL_U
            read -p "   邮箱授权码（非登录密码）: " EMAIL_P
            read -p "   收件邮箱地址（多个用逗号隔开）: " EMAIL_T
            if [ -n "$EMAIL_U" ]; then
                echo "" >> .env
                echo "EMAIL_USER=$EMAIL_U" >> .env
                echo "EMAIL_PASSWORD=$EMAIL_P" >> .env
                echo "EMAIL_TO=$EMAIL_T" >> .env
                echo "   邮件配置完成 ✓"
            fi
            ;;
        3)
            read -p "   请输入钉钉 Webhook URL: " DD_URL
            if [ -n "$DD_URL" ]; then
                echo "" >> .env
                echo "DINGTALK_WEBHOOK=$DD_URL" >> .env
                echo "   钉钉配置完成 ✓"
            fi
            ;;
        4)
            read -p "   请输入企业微信 Webhook URL: " WECOM_URL
            if [ -n "$WECOM_URL" ]; then
                echo "" >> .env
                echo "WECOM_WEBHOOK=$WECOM_URL" >> .env
                echo "   企业微信配置完成 ✓"
            fi
            ;;
        5)
            read -p "   Telegram Bot Token: " TG_TOKEN
            read -p "   Telegram Chat ID: " TG_CHAT
            if [ -n "$TG_TOKEN" ]; then
                echo "" >> .env
                echo "TELEGRAM_BOT_TOKEN=$TG_TOKEN" >> .env
                echo "TELEGRAM_CHAT_ID=$TG_CHAT" >> .env
                echo "   Telegram 配置完成 ✓"
            fi
            ;;
        6)
            echo "   请稍后手动编辑 .env 文件"
            ;;
    esac
fi

# ── Step 4: 微信登录 ──
echo ""
echo "[4/5] 微信公众号平台登录"
echo "   需要用绑定了公众号的微信扫码（免费订阅号即可）"
echo ""
read -p "   现在扫码登录？(Y/n): " DO_LOGIN
DO_LOGIN=${DO_LOGIN:-Y}

if [ "$DO_LOGIN" = "Y" ] || [ "$DO_LOGIN" = "y" ]; then
    .venv/bin/python main.py --login
fi

# ── Step 5: 测试运行 ──
echo ""
echo "[5/5] 测试运行"
read -p "   运行测试模式验证配置？(Y/n): " DO_TEST
DO_TEST=${DO_TEST:-Y}

if [ "$DO_TEST" = "Y" ] || [ "$DO_TEST" = "y" ]; then
    echo "   运行中（每个公众号取 1 篇）..."
    .venv/bin/python main.py --test
fi

# ── 完成 ──
echo ""
echo "=================================="
echo "  ✅ 配置完成！"
echo "=================================="
echo ""
echo "  常用命令："
echo "    source .venv/bin/activate"
echo "    python3 main.py              # 正式运行"
echo "    python3 main.py --dry-run    # 试运行（不推送）"
echo "    python3 main.py --test       # 测试模式"
echo "    python3 main.py --setup-cron # 配置定时任务"
echo ""
echo "  配置文件："
echo "    .env          — AI 模型 + 推送渠道"
echo "    config.yaml   — 公众号列表 + 评分维度 + 其他配置"
echo ""
