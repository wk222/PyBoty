import os
import subprocess
import sys
from pathlib import Path
import time

def run_tests():
    test_dir = Path("tests")
    log_file = Path("test_failures.log")
    summary_file = Path("test_summary.txt")
    
    # 获取所有测试文件
    test_files = sorted(list(test_dir.glob("test_*.py")))
    total_files = len(test_files)
    
    print(f"🚀 开始串行扫描测试，共计 {total_files} 个文件...")
    
    passed_files = 0
    failed_files = 0
    start_time = time.time()
    
    # 清空旧日志
    log_file.write_text("=== PyBot 架构重构失败详志 ===\n\n", encoding="utf-8")
    
    for i, test_file in enumerate(test_files, 1):
        print(f"[{i}/{total_files}] 正在测试: {test_file.name}...", end="", flush=True)
        
        try:
            # 运行单文件测试，增加超时保护
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_file), "--basetemp=C:\\pytest_tmp_robust", "-v", "--no-header"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=120  # 单文件 2 分钟超时
            )
            
            if result.returncode == 0:
                print(" ✅ 通过")
                passed_files += 1
            else:
                print(" ❌ 失败")
                failed_files += 1
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(f"\n{'='*80}\n")
                    f.write(f"失败文件: {test_file}\n")
                    f.write(f"退出代码: {result.returncode}\n")
                    f.write(f"{'-'*40} stdout {'-'*40}\n")
                    f.write(result.stdout)
                    f.write(f"{'-'*40} stderr {'-'*40}\n")
                    f.write(result.stderr)
                    f.write(f"\n{'='*80}\n")
                    
        except subprocess.TimeoutExpired:
            print(" ⏳ 超时")
            failed_files += 1
            with log_file.open("a", encoding="utf-8") as f:
                f.write(f"\n[TIMEOUT] {test_file} 执行超过 120 秒已强行终止\n")
        except Exception as e:
            print(f" ⚠️ 运行出错: {e}")
            failed_files += 1

    duration = time.time() - start_time
    summary = (
        f"\n测试任务完成！\n"
        f"耗时: {duration:.2f}秒\n"
        f"总文件数: {total_files}\n"
        f"成功文件: {passed_files}\n"
        f"失败文件: {failed_files}\n"
    )
    
    print(summary)
    summary_file.write_text(summary, encoding="utf-8")
    
    if failed_files > 0:
        print(f"🛑 发现失败项，请查看详细日志: {log_file.absolute()}")
    else:
        print("🎉 恭喜！所有测试文件均已通过。")

if __name__ == "__main__":
    # 确保临时目录存在
    os.makedirs("C:\\pytest_tmp_robust", exist_ok=True)
    run_tests()
