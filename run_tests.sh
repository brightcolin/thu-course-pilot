#!/bin/bash
echo "========== 运行所有测试 =========="
cd "$(dirname "$0")"
python -m pytest tests/ -v --tb=short
echo ""
echo "========== 测试完成 =========="
