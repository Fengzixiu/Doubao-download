"""
团子素材解析工具 - 核心解码引擎

本模块是整个项目最核心的技术部分，实现了跨架构的二进制模拟执行：
- 使用 Unicorn Engine 在 x86_64 平台上模拟执行 ARM64 架构的解码算法
- 手动解析 ELF 文件格式，加载共享库到模拟内存空间
- 处理 ELF 重定位表，映射外部函数引用
- 模拟系统调用（malloc/memcpy/memset/free/time/rand等）
- 执行加密URL的解密算法，返回真实媒体地址

技术原理：
豆包平台的媒体URL使用了自定义加密算法，该算法编译为ARM64架构的共享库。
由于项目运行在x86_64 Windows平台上，无法直接执行ARM64代码，因此需要：
1. 使用Unicorn Engine创建ARM64虚拟CPU环境
2. 将ELF文件加载到虚拟内存中
3. 模拟系统调用和内存管理
4. 在虚拟环境中执行解码函数
5. 读取解码结果并返回

内存布局设计：
- 0x1000-0x...    libvideodec.so代码/数据段（动态映射）
- 0xC00-0xC40     系统调用模拟入口点
- 0x70000000      栈空间（2MB）
- 0x71000000      堆空间（4MB）
- 0x72000000      TLS（线程本地存储，4KB）
- 0x73000000      输入/输出数据区（64KB）
- 0xDEAD0000      返回地址（终止点）

安全机制：
- 超时控制：30秒执行时间限制
- 指令限制：最多执行800万条ARM64指令
- 非法内存访问钩子：拒绝所有非法内存访问
- 内存隔离：使用独立的虚拟地址空间
"""

import base64
import json
import os
import struct
import sys
from pathlib import Path


def _align_down(value: int, alignment: int = 0x1000) -> int:
    """向下对齐到指定边界（默认4KB）
    
    Args:
        value: 要对齐的值
        alignment: 对齐边界，默认0x1000（4KB）
    
    Returns:
        向下对齐后的地址
    """
    return value & ~(alignment - 1)


def _align_up(value: int, alignment: int = 0x1000) -> int:
    """向上对齐到指定边界（默认4KB）
    
    Args:
        value: 要对齐的值
        alignment: 对齐边界，默认0x1000（4KB）
    
    Returns:
        向上对齐后的地址
    """
    return (value + alignment - 1) & ~(alignment - 1)


def _so_path() -> Path:
    """获取解码核心库的路径
    
    优先从环境变量 DOUBAO_VIDEODEC_SO 获取，否则使用默认路径：
    app/vendor/libvideodec.so
    
    Returns:
        libvideodec.so 文件的绝对路径
    """
    configured = os.environ.get("DOUBAO_VIDEODEC_SO", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "app" / "vendor" / "libvideodec.so"


def _read_u64(uc, addr: int) -> int:
    """从模拟内存中读取64位无符号整数
    
    Args:
        uc: Unicorn模拟器实例
        addr: 内存地址
    
    Returns:
        读取的64位整数值（小端序）
    """
    return struct.unpack("<Q", bytes(uc.mem_read(addr, 8)))[0]


def _write_u64(uc, addr: int, value: int) -> None:
    """向模拟内存中写入64位无符号整数
    
    Args:
        uc: Unicorn模拟器实例
        addr: 内存地址
        value: 要写入的64位整数值
    """
    uc.mem_write(addr, struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))


def decode_main_url(main_url: str, key_seed: str) -> str:
    """解码加密的媒体URL
    
    这是对外暴露的主要解码函数，处理以下逻辑：
    1. 如果URL已经是HTTP/HTTPS格式，直接返回
    2. 如果缺少参数，返回原始输入
    3. Base64解码原始数据和密钥
    4. 调用ARM64模拟引擎执行解密
    5. 验证解密结果，确保是有效的URL
    
    Args:
        main_url: 加密的URL字符串（通常是Base64编码）
        key_seed: 解密密钥字符串（通常是Base64编码）
    
    Returns:
        解密后的真实媒体URL，如果解密失败则返回原始输入
    """
    text = (main_url or "").strip()
    
    # 如果已经是完整的URL，直接返回
    if text.startswith(("http://", "https://")):
        return text
    
    # 参数校验：缺少必要参数时直接返回
    if not text or not key_seed:
        return text
    
    # Base64解码：添加必要的填充字符（=）
    raw = base64.b64decode(text + "=" * (-len(text) % 4))
    seed = base64.b64decode(key_seed + "=" * (-len(key_seed) % 4))
    
    # 调用核心解码引擎（ARM64模拟执行）
    decoded = _decode_with_core(raw, seed)
    
    # 验证结果：必须是有效的HTTP/HTTPS URL
    return decoded if decoded.startswith(("http://", "https://")) else text


def _decode_with_core(raw: bytes, seed: bytes) -> str:
    """核心解码函数：使用Unicorn模拟ARM64代码执行
    
    这是整个项目最核心的技术实现，包含以下步骤：
    1. 初始化Unicorn ARM64模拟器
    2. 解析ELF文件，加载代码和数据段到模拟内存
    3. 处理重定位表，映射外部函数引用
    4. 分配模拟内存空间（栈、堆、TLS、数据区）
    5. 设置系统调用钩子，模拟malloc/memcpy等函数
    6. 设置寄存器参数，调用解码函数
    7. 执行模拟，读取解码结果
    
    Args:
        raw: Base64解码后的加密数据
        seed: Base64解码后的密钥数据
    
    Returns:
        解密后的URL字符串
    """
    # 延迟导入：仅在需要时加载Unicorn和ELF解析库
    from elftools.elf.elffile import ELFFile
    from unicorn import Uc, UC_ARCH_ARM64, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_MODE_ARM, UC_PROT_ALL
    from unicorn.arm64_const import (
        UC_ARM64_REG_PC,
        UC_ARM64_REG_SP,
        UC_ARM64_REG_TPIDR_EL0,
        UC_ARM64_REG_X0,
        UC_ARM64_REG_X1,
        UC_ARM64_REG_X2,
        UC_ARM64_REG_X3,
        UC_ARM64_REG_X4,
        UC_ARM64_REG_X30,
    )

    # 获取解码核心库路径
    so_path = _so_path()
    if not so_path.exists():
        return ""

    # 步骤1：初始化Unicorn ARM64模拟器
    # UC_ARCH_ARM64: 指定架构为ARM64
    # UC_MODE_ARM: 使用ARM模式（非Thumb模式）
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    
    # 读取ELF文件内容
    blob = so_path.read_bytes()
    
    # 步骤2：解析ELF文件，加载代码和数据段
    with so_path.open("rb") as handle:
        elf = ELFFile(handle)
        
        # 遍历所有程序段（PT_LOAD类型）
        for segment in elf.iter_segments():
            if segment["p_type"] != "PT_LOAD":
                continue
            
            # 获取段信息
            vaddr = segment["p_vaddr"]      # 虚拟地址
            offset = segment["p_offset"]     # 文件偏移
            filesz = segment["p_filesz"]     # 文件中的大小
            memsz = segment["p_memsz"]       # 内存中的大小
            
            # 计算映射范围（向下对齐起始地址，向上对齐结束地址）
            start = _align_down(vaddr)
            size = _align_up(vaddr + memsz) - start
            
            # 在模拟器中映射内存区域
            uc.mem_map(start, size, UC_PROT_ALL)
            
            # 将ELF段内容写入模拟内存
            uc.mem_write(vaddr, blob[offset : offset + filesz])

        # 步骤3：处理重定位表（.rela.dyn 和 .rela.plt）
        # 重定位表用于解析共享库中对外部函数的引用
        
        # 定义外部函数到模拟地址的映射
        imported_functions = {
            "malloc": 0xC10,           # 内存分配
            "memcpy": 0xC20,           # 内存拷贝
            "memset": 0xC30,           # 内存填充
            "free": 0xC40,             # 内存释放
            "__stack_chk_fail": 0xC00, # 栈检查失败处理
            "time": 0xBE0,             # 获取时间
            "srand": 0xBD0,            # 设置随机种子
            "rand": 0xBF0,             # 获取随机数
        }
        
        # 遍历重定位段
        for section_name in (".rela.dyn", ".rela.plt"):
            section = elf.get_section_by_name(section_name)
            if not section:
                continue
            
            # 获取符号表（用于解析重定位中的符号名称）
            symtab = elf.get_section(section["sh_link"]) if section["sh_link"] else None
            
            # 遍历每个重定位条目
            for reloc in section.iter_relocations():
                r_type = reloc["r_info_type"]  # 重定位类型
                where = reloc["r_offset"]      # 重定位目标地址
                addend = reloc["r_addend"] if reloc.is_RELA() else 0  # 加数
                
                value = None
                sym_name = ""
                sym_value = 0
                
                # 获取符号信息
                if symtab is not None and reloc["r_info_sym"]:
                    sym = symtab.get_symbol(reloc["r_info_sym"])
                    sym_name = sym.name
                    sym_value = sym["st_value"]
                
                # 根据重定位类型处理
                if r_type == 1027:
                    # R_AARCH64_RELATIVE: 相对重定位
                    value = addend
                elif r_type in (257, 1025, 1026):
                    # R_AARCH64_ABS64 / R_AARCH64_JUMP_SLOT / R_AARCH64_GLOB_DAT
                    # 映射到模拟的系统调用地址
                    value = imported_functions.get(sym_name, sym_value + addend)
                
                # 将解析后的地址写入目标位置
                if value is not None:
                    uc.mem_write(where, struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))

    # 步骤4：分配模拟内存空间
    # 设计独立的内存布局，避免与真实系统冲突
    
    stack = 0x70000000      # 栈空间起始地址
    stack_size = 0x200000   # 栈大小：2MB
    heap = 0x71000000       # 堆空间起始地址
    heap_size = 0x400000    # 堆大小：4MB
    tls = 0x72000000        # TLS（线程本地存储）地址
    data = 0x73000000       # 输入/输出数据区地址
    ret_addr = 0xDEAD0000   # 返回地址（解码完成后跳转到这里）
    
    # 映射所有内存区域
    for addr, size in (
        (stack, stack_size),
        (heap, heap_size),
        (tls, 0x1000),       # TLS: 4KB
        (data, 0x10000),     # 数据区: 64KB
        (ret_addr, 0x1000),  # 返回地址: 4KB
    ):
        uc.mem_map(addr, size, UC_PROT_ALL)

    # 步骤5：初始化TLS（线程本地存储）
    # ARM64使用TPIDR_EL0寄存器存储TLS基地址
    uc.mem_write(tls + 0x28, struct.pack("<Q", 0x1122334455667788))
    uc.reg_write(UC_ARM64_REG_TPIDR_EL0, tls)
    
    # 初始化堆指针
    heap_next = heap

    # 步骤6：设置系统调用钩子
    # 当ARM64代码调用外部函数时，通过这个钩子捕获并模拟执行
    
    def hook_code(uc, address, _size, _user):
        """代码执行钩子：模拟系统调用
        
        当代码执行到预设的系统调用地址时，捕获并模拟相应的系统函数。
        
        Args:
            uc: Unicorn模拟器实例
            address: 当前执行的指令地址
            _size: 指令大小（未使用）
            _user: 用户数据（未使用）
        """
        nonlocal heap_next
        
        # 仅处理预设的系统调用地址
        if address not in (0xC10, 0xC20, 0xC30, 0xC40, 0xC00, 0xBD0, 0xBE0, 0xBF0):
            return
        
        # 保存返回地址（X30寄存器）
        lr = uc.reg_read(UC_ARM64_REG_X30)
        
        if address == 0xC10:  # malloc - 内存分配
            amount = uc.reg_read(UC_ARM64_REG_X0)  # 参数1：分配大小
            ptr = _align_up(heap_next, 16)         # 16字节对齐
            heap_next = ptr + _align_up(amount or 1, 16)  # 更新堆指针
            uc.mem_write(ptr, b"\x00" * _align_up(amount or 1, 16))  # 清零内存
            uc.reg_write(UC_ARM64_REG_X0, ptr)     # 返回分配的地址
        
        elif address == 0xC20:  # memcpy - 内存拷贝
            dst = uc.reg_read(UC_ARM64_REG_X0)     # 参数1：目标地址
            src = uc.reg_read(UC_ARM64_REG_X1)     # 参数2：源地址
            amount = uc.reg_read(UC_ARM64_REG_X2)  # 参数3：拷贝大小
            uc.mem_write(dst, bytes(uc.mem_read(src, amount)))  # 执行拷贝
            uc.reg_write(UC_ARM64_REG_X0, dst)     # 返回目标地址
        
        elif address == 0xC30:  # memset - 内存填充
            dst = uc.reg_read(UC_ARM64_REG_X0)     # 参数1：目标地址
            value = uc.reg_read(UC_ARM64_REG_X1) & 0xFF  # 参数2：填充值（取低8位）
            amount = uc.reg_read(UC_ARM64_REG_X2)  # 参数3：填充大小
            uc.mem_write(dst, bytes([value]) * amount)  # 执行填充
            uc.reg_write(UC_ARM64_REG_X0, dst)     # 返回目标地址
        
        elif address == 0xC40:  # free - 内存释放（空实现）
            uc.reg_write(UC_ARM64_REG_X0, 0)       # 返回0
        
        elif address == 0xC00:  # __stack_chk_fail - 栈检查失败
            raise RuntimeError("videodec stack check failed")
        
        elif address == 0xBE0:  # time - 获取时间戳（返回固定值）
            uc.reg_write(UC_ARM64_REG_X0, 1783394639)  # 固定时间戳
        
        elif address == 0xBD0:  # srand - 设置随机种子（空实现）
            uc.reg_write(UC_ARM64_REG_X0, 0)
        
        elif address == 0xBF0:  # rand - 获取随机数（返回固定值）
            uc.reg_write(UC_ARM64_REG_X0, 0x12345678)
        
        # 跳回调用点（模拟函数返回）
        uc.reg_write(UC_ARM64_REG_PC, lr)

    def hook_invalid(_uc, _access, _address, _size, _value, _user):
        """非法内存访问钩子
        
        当代码尝试访问未映射的内存区域时，拒绝访问并终止执行。
        
        Returns:
            False: 拒绝访问
        """
        return False

    def hook_stop(uc, address, _size, _user):
        """终止钩子：检测解码完成
        
        当代码跳转到 ret_addr（0xDEAD0000）时，说明解码完成，终止模拟。
        
        Args:
            uc: Unicorn模拟器实例
            address: 当前执行地址
        """
        if address == ret_addr:
            uc.emu_stop()

    # 注册钩子
    uc.hook_add(UC_HOOK_CODE, hook_code)           # 系统调用钩子
    uc.hook_add(UC_HOOK_MEM_INVALID, hook_invalid) # 非法内存访问钩子
    uc.hook_add(UC_HOOK_CODE, hook_stop, begin=ret_addr, end=ret_addr + 4)  # 终止钩子

    # 步骤7：准备输入数据
    inp = data              # 输入数据地址
    key = data + 0x1000     # 密钥地址（偏移4KB）
    out = data + 0x2000     # 输出数据地址（偏移8KB）
    out_len = data + 0x5000 # 输出长度地址（偏移20KB）
    
    uc.mem_write(inp, raw)              # 写入加密数据
    uc.mem_write(key, seed)             # 写入密钥
    uc.mem_write(out, b"\x00" * 0x3000) # 预分配输出缓冲区（12KB）
    _write_u64(uc, out_len, len(raw) - 4)  # 设置输出长度初始值

    # 步骤8：设置寄存器参数，调用解码函数
    # ARM64调用约定：前6个参数使用X0-X5寄存器
    # 返回地址使用X30寄存器
    
    uc.reg_write(UC_ARM64_REG_SP, stack + stack_size - 0x10)  # 设置栈指针
    uc.reg_write(UC_ARM64_REG_X0, inp)     # 参数1：输入数据地址
    uc.reg_write(UC_ARM64_REG_X1, len(raw))  # 参数2：输入数据长度
    uc.reg_write(UC_ARM64_REG_X2, out)     # 参数3：输出缓冲区地址
    uc.reg_write(UC_ARM64_REG_X3, out_len)  # 参数4：输出长度地址
    uc.reg_write(UC_ARM64_REG_X4, key)     # 参数5：密钥地址
    uc.reg_write(UC_ARM64_REG_X30, ret_addr)  # 返回地址
    
    # 步骤9：开始模拟执行
    # 从地址0x1364开始执行（解码函数入口）
    # 超时30秒，最多执行800万条指令
    uc.emu_start(0x1364, ret_addr, timeout=30_000_000, count=8_000_000)

    # 步骤10：读取解码结果
    result_len = _read_u64(uc, out_len)  # 获取实际输出长度
    return bytes(uc.mem_read(out, min(result_len, 0x3000))).decode("utf-8", "ignore")


def main() -> int:
    """命令行入口函数
    
    从标准输入读取JSON格式的请求，解码后输出JSON格式的结果。
    
    Returns:
        0: 解码成功
        1: 解码失败（异常）
        2: 解码失败（无结果）
    """
    try:
        # 从标准输入读取JSON
        payload = json.loads(sys.stdin.read() or "{}")
        
        # 调用解码函数
        url = decode_main_url(
            str(payload.get("main_url") or ""),
            str(payload.get("key_seed") or "")
        )
        
        # 输出结果
        print(json.dumps({"url": url}, ensure_ascii=False))
        
        # 返回状态码
        return 0 if url else 2
    
    except Exception as exc:
        # 输出错误信息到标准错误
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
