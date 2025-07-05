# -*- coding: utf-8 -*-
import abc
import io
import tempfile
from io import StringIO
from typing import TYPE_CHECKING, Dict, List, Optional
from collections import Counter
import re
import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract
import os
from datetime import datetime

import pandas as pd
import pywinauto.keyboard
import pywinauto
import pywinauto.clipboard

from easytrader.log import logger
from easytrader.utils.captcha import captcha_recognize
from easytrader.utils.win_gui import ShowWindow, win32defines
from easytrader.utils.win_gui import SetForegroundWindow  # type: ignore

if TYPE_CHECKING:
    # pylint: disable=unused-import
    from easytrader import clienttrader  # type: ignore

# 相似字符映射表 (常见混淆对)
SIMILAR_CHARS = {
    'O': '0', '0': 'O',
    '1': 'I', 'I': '1',
    '2': 'Z', 'Z': '2',
    '5': 'S', 'S': '5',
    '8': 'B', 'B': '8',
    '6': 'G', 'G': '6',
    'Q': 'O', 'D': 'O',
    'U': 'V', 'V': 'U'
}

class IGridStrategy(abc.ABC):
    @abc.abstractmethod
    def get(self, control_id: int) -> List[Dict]:
        pass

    @abc.abstractmethod
    def set_trader(self, trader: "clienttrader.IClientTrader"):
        pass

class BaseStrategy(IGridStrategy):
    _trader: Optional["clienttrader.IClientTrader"] = None
    
    def __init__(self):
        self._trader = None

    def set_trader(self, trader: "clienttrader.IClientTrader"):
        self._trader = trader

    @abc.abstractmethod
    def get(self, control_id: int) -> List[Dict]:
        pass

    def _get_grid(self, control_id: int):
        if not self._trader or not hasattr(self._trader, 'main'):
            raise ValueError("Trader not properly initialized")
        return self._trader.main.child_window(
            control_id=control_id, class_name="CVirtualGridCtrl"
        )

    def _set_foreground(self, grid=None):
        if not self._trader:
            raise ValueError("Trader not initialized")
        try:
            if grid is None:
                if not hasattr(self._trader, 'main'):
                    raise ValueError("Trader has no main window")
                grid = self._trader.main
            if grid.has_style(win32defines.WS_MINIMIZE):
                ShowWindow(grid.wrapper_object(), 9)
            else:
                SetForegroundWindow(grid.wrapper_object())
        except:
            pass

class Copy(BaseStrategy):
    _need_captcha_reg = True

    def get(self, control_id: int) -> List[Dict]:
        grid = self._get_grid(control_id)
        self._set_foreground(grid)
        grid.type_keys("^A^C", set_foreground=False)
        content = self._get_clipboard_data()
        return self._format_grid_data(content)

    def _format_grid_data(self, data: str) -> List[Dict]:
        if not self._trader or not hasattr(self._trader, 'config'):
            raise ValueError("Trader or config not properly initialized")
        try:
            df = pd.read_csv(
                io.StringIO(data),
                delimiter="\t",
                dtype=self._trader.config.GRID_DTYPE,
                na_filter=False,
            )
            return df.to_dict("records")
        except:
            Copy._need_captcha_reg = True
            return []

    def _get_clipboard_data(self) -> str:
        if not self._trader or not hasattr(self._trader, 'app'):
            raise ValueError("Trader or app not properly initialized")
            
        if Copy._need_captcha_reg:
            if self._trader.app.top_window().window(class_name="Static", title_re="验证码").exists(timeout=1):
                file_path = "tmp.png"
                count = 5
                found = False
                while count > 0:
                    self._trader.app.top_window().window(
                        control_id=0x965, class_name="Static"
                    ).capture_as_image().save(file_path)

                    try:
                        manual_input = getattr(self._trader._config, 'CAPTCHA_MANUAL_INPUT', False)
                        if manual_input:
                            from easytrader.utils.captcha import input_verify_code_manual
                            captcha_num = input_verify_code_manual(file_path).strip()
                        else:
                            captcha_num = captcha_recognize(file_path).strip()
                    except Exception as e:
                        logger.error(f"验证码识别失败: {e}")
                        captcha_num = ""
                    
                    captcha_num = "".join(captcha_num.split())
                    logger.info("captcha result-->" + captcha_num)
                    if len(captcha_num) == 4:
                        editor = self._trader.app.top_window().window(
                            control_id=0x964, class_name="Edit"
                        )
                        self._trader.type_edit_control_keys(editor, captcha_num)
                        self._trader.app.top_window().set_focus()
                        pywinauto.keyboard.SendKeys("{ENTER}")
                        try:
                            logger.info(
                                self._trader.app.top_window()
                                    .window(control_id=0x966, class_name="Static")
                                    .window_text()
                            )
                        except Exception as ex:
                            logger.exception(ex)
                            found = True
                            break
                    count -= 1
                    if not found:
                        if not self._trader or not hasattr(self._trader, 'app'):
                            raise ValueError("Trader or app not properly initialized")
                        self._trader.app.top_window().Button2.click()
            if not self._trader or not hasattr(self._trader, 'wait'):
                raise ValueError("Trader or wait method not properly initialized")
            self._trader.wait(0.1)
            if not self._trader or not hasattr(self._trader, 'app'):
                raise ValueError("Trader or app not properly initialized")
            try:
                app_window = self._trader.app.top_window()
                if not app_window:
                    raise ValueError("Failed to get app top window")
                app_window.window(
                    control_id=0x965, class_name="Static"
                ).click()
            except Exception as e:
                logger.error(f"Failed to access app window: {e}")
                raise
        else:
            Copy._need_captcha_reg = False
        count = 5
        while count > 0:
            try:
                return pywinauto.clipboard.GetData()
            except Exception as e:
                count -= 1
                logger.exception("%s, retry ......", e)
        return ""

class WMCopy(Copy):
    def get(self, control_id: int) -> List[Dict]:
        grid = self._get_grid(control_id)
        grid.post_message(win32defines.WM_COMMAND, 0xE122, 0)
        self._trader.wait(0.1)
        content = self._get_clipboard_data()
        return self._format_grid_data(content)

class Xls(BaseStrategy):
    CAPTCHA_CONFIG = {
        'max_retry_count': 5,
        'input_wait_time': 0.8,
        'refresh_wait_time': 0.6,
        'success_timeout': 0.8,
        'use_fast_mode': True,
        'smart_case_order': True,
    }

    def set_captcha_mode(self, mode="balanced"):
        if mode == "fast":
            self.CAPTCHA_CONFIG.update({
                'max_retry_count': 3,
                'input_wait_time': 0.5,
                'refresh_wait_time': 0.4,
                'success_timeout': 0.5,
                'use_fast_mode': True,
            })
            logger.info("🚀 已切换到快速模式")
        elif mode == "accurate":
            self.CAPTCHA_CONFIG.update({
                'max_retry_count': 8,
                'input_wait_time': 1.2,
                'refresh_wait_time': 1.0,
                'success_timeout': 1.5,
                'use_fast_mode': False,
            })
            logger.info("🎯 已切换到准确模式")
        else:
            self.CAPTCHA_CONFIG.update({
                'max_retry_count': 5,
                'input_wait_time': 0.8,
                'refresh_wait_time': 0.6,
                'success_timeout': 0.8,
                'use_fast_mode': True,
            })
            logger.info("⚖️ 已切换到平衡模式")
    
    def get_captcha_config(self):
        return self.CAPTCHA_CONFIG.copy()
    
    def update_captcha_config(self, **kwargs):
        self.CAPTCHA_CONFIG.update(kwargs)
        logger.info(f"🔧 已更新验证码配置: {kwargs}")

    def __init__(self, tmp_folder: Optional[str] = None):
        super().__init__()
        self.tmp_folder = tmp_folder

    def get(self, control_id: int) -> List[Dict]:
        logger.info("保存 grid 内容为 xls 文件模式")
        
        grid = None
        try:
            grid = self._get_grid(control_id)
            logger.info(f"成功获取表格控件，control_id: {control_id}")
        except Exception as e:
            logger.error(f"无法获取表格控件 {control_id}: {e}")
            try:
                grids = self._trader.main.child_windows(class_name="CVirtualGridCtrl")
                if grids:
                    grid = grids[0]
                    logger.info(f"使用第一个找到的表格控件: {grid.control_id()}")
                else:
                    logger.error("未找到任何 CVirtualGridCtrl 控件")
                    raise Exception("无法找到数据表格控件")
            except Exception as e2:
                logger.error(f"查找表格控件失败: {e2}")
                raise Exception("无法找到数据表格控件")
        
        logger.info("设置表格控件焦点...")
        try:
            grid.click()
            self._trader.wait(0.5)
            self._set_foreground(grid)
            self._trader.wait(0.5)
            grid.set_focus()
            self._trader.wait(0.5)
            logger.info("表格控件焦点设置完成")
        except Exception as e:
            logger.warning(f"设置表格焦点时出错: {e}")
        
        logger.info("发送 Ctrl+S 命令...")
        try:
            grid.type_keys("^s", set_foreground=False)
            logger.info("Ctrl+S 命令已发送")
        except Exception as e:
            logger.error(f"发送 Ctrl+S 失败: {e}")
            try:
                pywinauto.keyboard.SendKeys("^s")
                logger.info("使用备用方法发送 Ctrl+S")
            except Exception as e2:
                logger.error(f"备用方法也失败: {e2}")
                raise Exception("无法发送保存命令")
        
        self._trader.wait(2.0)
        
        logger.info("检查是否有验证码窗口...")
        if (self._trader.app.top_window().window(
            class_name="Static", title_re="验证码"
        ).exists(timeout=3)):
            logger.info("发现验证码窗口，开始处理验证码...")
            captcha_success = self._handle_captcha()
            if not captcha_success:
                logger.error("验证码处理失败，取消操作")
                if self._trader.app.top_window().window(title_re="取消|Cancel").exists(timeout=1):
                    self._trader.app.top_window().window(title_re="取消|Cancel").click()
                raise Exception("验证码处理失败")
        else:
            logger.info("未发现验证码窗口")
        
        logger.info("等待另存为对话框...")
        save_dialog_found = False
        for i in range(15):
            conditions = [
                lambda: self._trader.app.window(title_re='另存为|Save As|文件另存为').exists(timeout=0.2),
                lambda: self._trader.app.top_window().window(class_name="Edit", control_id=0x47C).exists(timeout=0.2),
                lambda: self._trader.app.top_window().window(class_name="#32770").exists(timeout=0.2) and "另存为" in self._trader.app.top_window().window_text(),
            ]
            
            for condition in conditions:
                try:
                    if condition():
                        save_dialog_found = True
                        logger.info(f"找到另存为对话框 (尝试 {i+1}/15)")
                        break
                except:
                    continue
            
            if save_dialog_found:
                break
                
            self._trader.wait(0.2)
            logger.info(f"等待另存为对话框... 尝试 {i+1}/15")
        
        if not save_dialog_found:
            try:
                current_window = self._trader.app.top_window()
                logger.error(f"当前窗口标题: {current_window.window_text()}")
                logger.error(f"当前窗口类名: {current_window.class_name()}")
                for child in current_window.children():
                    try:
                        logger.info(f"子窗口: {child.window_text()}, 类名: {child.class_name()}, ID: {child.control_id()}")
                    except:
                        pass
            except Exception as e:
                logger.error(f"获取窗口信息失败: {e}")
            
            logger.error("未找到另存为对话框，尝试重新发送 Ctrl+S")
            try:
                self._set_foreground(grid)
                grid.click()
                self._trader.wait(0.5)
                grid.type_keys("^s", set_foreground=False)
                self._trader.wait(2.0)
                
                if (self._trader.app.window(title_re='另存为|Save As|文件另存为').exists(timeout=1) or \
                   self._trader.app.top_window().window(class_name="Edit", control_id=0x47C).exists(timeout=1)):
                    save_dialog_found = True
                    logger.info("重新发送 Ctrl+S 后找到另存为对话框")
            except Exception as e:
                logger.error(f"重新发送 Ctrl+S 失败: {e}")
        
        if not save_dialog_found:
            raise Exception("无法找到另存为对话框，可能是表格为空或权限不足")
        
        temp_path = tempfile.mktemp(suffix=".xls", dir=self.tmp_folder)
        logger.info(f"设置保存路径: {temp_path}")
        
        try:
            if self._trader.app.window(title_re='另存为|Save As|文件另存为').exists(timeout=1):
                save_window = self._trader.app.window(title_re='另存为|Save As|文件另存为')
            else:
                save_window = self._trader.app.top_window()
                
            edit_control = None
            for control_id in [0x47C, 0x3E9, 1001]:
                try:
                    edit_control = save_window.window(class_name="Edit", control_id=control_id)
                    if edit_control.exists():
                        break
                except:
                    continue
            
            if edit_control is None:
                try:
                    edit_control = save_window.window(class_name="Edit")
                except:
                    pass
            
            if edit_control and edit_control.exists():
                edit_control.set_edit_text(temp_path)
                logger.info("成功设置文件路径")
            else:
                logger.error("无法找到文件名编辑框")
                save_window.type_keys(temp_path)
                
        except Exception as e:
            logger.error(f"设置文件路径失败: {e}")
            
        self._trader.wait(0.2)
        
        try:
            if self._trader.app.window(title_re='另存为|Save As|文件另存为').exists(timeout=1):
                save_window = self._trader.app.window(title_re='另存为|Save As|文件另存为')
            else:
                save_window = self._trader.app.top_window()
                
            save_window.type_keys("%{s}", set_foreground=False)
            logger.info("已发送保存命令")
        except Exception as e:
            logger.error(f"发送保存命令失败: {e}")
            
        self._trader.wait(0.5)
        
        if self._trader.app.window(title_re='确认另存为|Confirm Save As|替换|Replace').exists(timeout=1):
            logger.info("文件已存在，确认替换...")
            replace_window = self._trader.app.window(title_re='确认另存为|Confirm Save As|替换|Replace')
            replace_window.type_keys("%{y}", set_foreground=False)
            self._trader.wait(0.2)

        import os
        retry_count = 0
        max_retries = 20
        logger.info(f"等待文件保存完成: {temp_path}")
        while not os.path.exists(temp_path) and retry_count < max_retries:
            self._trader.wait(0.3)
            retry_count += 1
            logger.info(f"等待文件保存完成... 尝试 {retry_count}/{max_retries}")
            
            if self._trader.is_exist_pop_dialog():
                logger.info("发现弹窗，尝试关闭...")
                try:
                    self._trader.app.top_window().Button2.click()
                    self._trader.wait(0.2)
                except:
                    pass
        
        if not os.path.exists(temp_path):
            try:
                current_window = self._trader.app.top_window()
                logger.error(f"保存失败 - 当前窗口标题: {current_window.window_text()}")
                logger.error(f"保存失败 - 当前窗口类名: {current_window.class_name()}")
            except:
                pass
            raise FileNotFoundError(f"无法保存或找到临时文件: {temp_path}")

        logger.info(f"文件保存成功: {temp_path}")
        return self._format_grid_data(temp_path)

    def _format_grid_data(self, data: str) -> List[Dict]:
        try:
            with open(data, encoding="gbk", errors="replace") as f:
                content = f.read()

            df = pd.read_csv(
                StringIO(content),
                delimiter="\t",
                dtype=self._trader.config.GRID_DTYPE,
                na_filter=False,
            )
            return df.to_dict("records")
        except Exception as e:
            logger.error(f"格式化网格数据失败: {e}")
            return []
        finally:
            import os
            try:
                if os.path.exists(data):
                    os.remove(data)
                    logger.info(f"已删除临时文件: {data}")
            except Exception as e:
                logger.warning(f"删除临时文件失败: {data}, 错误: {e}")

    def _handle_captcha(self) -> bool:
        """处理验证码输入，返回是否成功"""
        if not self._trader or not hasattr(self._trader, 'app'):
            raise ValueError("Trader or app not properly initialized")
            
        import os
        from datetime import datetime
        
        debug_folder = "captcha_debug"
        if not os.path.exists(debug_folder):
            os.makedirs(debug_folder)
        
        file_path = "tmp.png"
        count = self.CAPTCHA_CONFIG['max_retry_count']
        found = False
        
        logger.info("检测到验证码窗口，开始自动识别...")
        
        while count > 0:
            try:
                # 保存验证码图片并增强分辨率
                captcha_img = self._trader.app.top_window().window(
                    control_id=0x965, class_name="Static"
                ).capture_as_image()
                
                # 超分辨率处理
                try:
                    import cv2
                    import numpy as np
                    
                    # 转换为OpenCV格式
                    img = cv2.cvtColor(np.array(captcha_img), cv2.COLOR_RGB2BGR)
                    
                    # 双三次插值放大2倍
                    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                    
                    # 锐化处理
                    kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
                    img = cv2.filter2D(img, -1, kernel)
                    
                    # 保存处理后的图片
                    cv2.imwrite(file_path, img)
                except Exception as e:
                    logger.warning(f"超分辨率处理失败: {e}")
                    captcha_img.save(file_path)
                
                # 同时保存调试副本
                debug_filename = f"captcha_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{9-count}.png"
                debug_path = os.path.join(debug_folder, debug_filename)
                try:
                    import shutil
                    shutil.copy2(file_path, debug_path)
                    logger.info(f"验证码图片已保存到: {debug_path}")
                except:
                    pass
                
                # 改进验证码识别
                captcha_num = self._recognize_captcha_improved(file_path)
                logger.info(f"验证码识别结果: {captcha_num}")
                
                if len(captcha_num) == 4 and captcha_num.isalnum():
                    # 只尝试原始识别结果
                    logger.info(f"尝试验证码: {captcha_num}")
                    
                    # 输入验证码
                    editor = self._trader.app.top_window().window(
                        control_id=0x964, class_name="Edit"
                    )
                    editor.select()
                    # 清空输入框
                    editor.type_keys("^a")  # Ctrl+A 全选
                    editor.type_keys("{DELETE}")  # 删除
                    if not self._trader or not hasattr(self._trader, 'app') or not editor:
                        raise ValueError("Trader, app or editor not properly initialized")
                    if not self._trader or not hasattr(self._trader, 'wait'):
                        raise ValueError("Trader or wait method not properly initialized")
                    self._trader.wait(0.1)
                    editor.type_keys(captcha_num)
                    
                    # 确认输入
                    self._trader.app.top_window().set_focus()
                    pywinauto.keyboard.SendKeys("{ENTER}")
                    
                    # 等待系统响应
                    self._trader.wait(self.CAPTCHA_CONFIG['input_wait_time'])
                    
                    # 检查是否成功
                    success_detected = False
                    
                    try:
                        if not self._trader.app.top_window().window(
                            class_name="Static", title_re="验证码").exists(timeout=self.CAPTCHA_CONFIG['success_timeout']):
                            success_detected = True
                            logger.info(f"🎉 验证码输入成功: {captcha_num}")
                    except:
                        success_detected = True
                        logger.info(f"🎉 验证码可能输入成功: {captcha_num}")
                    
                    if success_detected:
                        found = True
                        # 保存成功的验证码记录
                        success_record = f"验证码: {captcha_num}, 图片: {debug_filename}"
                        try:
                            with open(os.path.join(debug_folder, "success_log.txt"), "a", encoding="utf-8") as f:
                                f.write(f"{datetime.now()}: {success_record}\n")
                        except:
                            pass
                    else:
                        logger.info(f"❌ 验证码 {captcha_num} 输入失败")
                    
                    if found:
                        break
                        
                else:
                    logger.info(f"验证码识别结果无效: '{captcha_num}' (长度: {len(captcha_num)}, 是否为字母数字: {captcha_num.isalnum() if captcha_num else False})")
                
            except Exception as e:
                logger.error(f"验证码处理出错: {e}")
            
            if found:
                break
                
            count -= 1
            self._trader.wait(0.3)
            
            # 只有验证码输入失败时才刷新
            if not found:
                try:
                    self._trader.app.top_window().window(
                        control_id=0x965, class_name="Static"
                    ).click()
                    self._trader.wait(self.CAPTCHA_CONFIG['refresh_wait_time'])
                    logger.info(f"🔄 验证码输入失败，已刷新验证码，剩余尝试次数: {count}")
                except Exception as e:
                    logger.warning(f"刷新验证码失败: {e}")
        
        if not found:
            logger.error("❌ 验证码识别失败，取消操作")
            logger.info(f"请检查 {debug_folder} 文件夹中的验证码图片进行调试")
            try:
                self._trader.app.top_window().Button2.click()  # 点击取消
                self._trader.wait(0.2)
            except Exception as e:
                logger.error(f"取消验证码对话框失败: {e}")
        
        # 清理临时验证码图片
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.warning(f"删除验证码临时文件失败: {e}")
            
        return found

    def _recognize_captcha_improved(self, image_path: str) -> str:
        """改进的验证码识别函数 - 多种方法尝试"""
        if not self._trader:
            raise ValueError("Trader not initialized")
            
        results = []
        
        # 方法1: 基础预处理
        try:
            result1 = self._method1_basic_processing(image_path)
            if result1 and len(result1) == 4:
                results.append(result1)
                logger.info(f"方法1识别结果: {result1}")
        except Exception as e:
            logger.warning(f"方法1失败: {e}")
        
        # 方法2: 增强对比度
        try:
            result2 = self._method2_contrast_enhancement(image_path)
            if result2 and len(result2) == 4:
                results.append(result2)
                logger.info(f"方法2识别结果: {result2}")
        except Exception as e:
            logger.warning(f"方法2失败: {e}")
        
        # 方法3: 二值化 + 形态学处理
        try:
            result3 = self._method3_morphology(image_path)
            if result3 and len(result3) == 4:
                results.append(result3)
                logger.info(f"方法3识别结果: {result3}")
        except Exception as e:
            logger.warning(f"方法3失败: {e}")
        
        # 方法4: 多阈值尝试
        try:
            result4 = self._method4_multi_threshold(image_path)
            if result4 and len(result4) == 4:
                results.append(result4)
                logger.info(f"方法4识别结果: {result4}")
        except Exception as e:
            logger.warning(f"方法4失败: {e}")
        
        # 方法5: 简单优化方法 - 去除干扰点
        try:
            result5 = self._method5_simple_denoise(image_path)
            if result5 and len(result5) == 4:
                results.append(result5)
                logger.info(f"方法5识别结果: {result5}")
        except Exception as e:
            logger.warning(f"方法5失败: {e}")
        
        # 返回最常见的结果，或第一个有效结果
        if results:
            from collections import Counter
            # 尝试大小写变换
            all_variants = []
            for r in results:
                all_variants.extend([r, r.upper(), r.lower()])
            
            counter = Counter(all_variants)
            most_common = counter.most_common(1)[0][0]
            logger.info(f"最终选择结果: {most_common}, 所有结果: {results}")
            return most_common
        
        # 如果所有方法都失败，返回空字符串
        logger.error("所有识别方法都失败")
        return ""

    def _method1_basic_processing(self, image_path: str) -> str:
        """方法1: 基础图片处理"""
        from PIL import Image
        import pytesseract
        import re
        
        img = Image.open(image_path)
        img = img.convert('L')
        
        # 简单二值化
        img = img.point(lambda p: 0 if int(p) < 128 else 255, '1')  # type: ignore
        
        config = r'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
        result = pytesseract.image_to_string(img, config=config)
        
        valid_chars = re.findall(r'[0-9A-Za-z]', result)
        return ''.join(valid_chars)

    def _method2_contrast_enhancement(self, image_path: str) -> str:
        """方法2: 对比度增强"""
        from PIL import Image, ImageEnhance, ImageFilter
        import pytesseract
        import re
        
        img = Image.open(image_path)
        img = img.convert('L')
        
        # 增强对比度
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(3.0)
        
        # 锐化
        img = img.filter(ImageFilter.SHARPEN)
        
        # 二值化
        img = img.point(lambda p: 0 if int(p) < 140 else 255, '1')  # type: ignore
        
        config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
        result = pytesseract.image_to_string(img, config=config)
        
        valid_chars = re.findall(r'[0-9A-Za-z]', result)
        return ''.join(valid_chars)

    def _method3_morphology(self, image_path: str) -> str:
        """方法3: 形态学处理"""
        from PIL import Image, ImageFilter
        import pytesseract
        import re
        
        img = Image.open(image_path)
        img = img.convert('L')
        
        # 高斯模糊去噪
        img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
        
        # 二值化
        img = img.point(lambda p: 0 if int(p) < 120 else 255, '1')  # type: ignore
        
        # 中值滤波去噪
        img = img.filter(ImageFilter.MedianFilter(size=3))
        
        config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
        result = pytesseract.image_to_string(img, config=config)
        
        valid_chars = re.findall(r'[0-9A-Za-z]', result)
        return ''.join(valid_chars)

    def _method4_multi_threshold(self, image_path: str) -> str:
        """方法4: 多阈值尝试"""
        from PIL import Image
        import pytesseract
        import re
        
        img = Image.open(image_path)
        img = img.convert('L')
        
        # 尝试多个阈值
        thresholds = [100, 120, 140, 160, 180]
        results = []
        
        for threshold in thresholds:
            try:
                binary_img = img.point(lambda p: 0 if int(p) < threshold else 255, '1')  # type: ignore
                config = r'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
                result = pytesseract.image_to_string(binary_img, config=config)
                valid_chars = re.findall(r'[0-9A-Za-z]', result)
                clean_result = ''.join(valid_chars)
                if len(clean_result) == 4:
                    results.append(clean_result)
            except:
                continue
        
        # 返回最常见的结果
        if results:
            from collections import Counter
            counter = Counter(results)
            return counter.most_common(1)[0][0]
        
        return ""

    def _method5_simple_denoise(self, image_path: str) -> str:
        """方法5: 简单去噪优化"""
        from PIL import Image, ImageEnhance
        import pytesseract
        import re
        
        try:
            img = Image.open(image_path)
            img = img.convert('L')
            
            # 简单的亮度调整
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(1.2)
            
            # 使用稍低的阈值处理
            img = img.point(lambda p: 0 if int(p) < 110 else 255, '1')  # type: ignore
            
            # 配置更宽松的字符集
            config = r'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
            result = pytesseract.image_to_string(img, config=config)
            
            valid_chars = re.findall(r'[0-9A-Za-z]', result)
            return ''.join(valid_chars)
        except Exception as e:
            logger.warning(f"方法5执行失败: {e}")
            return ""
