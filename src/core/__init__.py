def _Documented(base_type: type) -> type:
    """工厂函数，创建携带文档字符串的不可变类型子类。

    为指定的不可变类型（如 str、tuple、int 等）生成一个子类，
    该子类在构造时接受额外的 doc 参数，并将其绑定到实例的 __doc__ 属性上。

    Args:
        base_type: 要继承的不可变基类型（必须是支持 __new__ 的不可变类型）。

    Returns:
        一个新的子类，其行为与 base_type 完全一致，但额外支持 doc 参数。
    """

    class _DocumentedSubclass(base_type):
        __annotated_type__ = base_type

        def __new__(cls, value, doc: str = ''):
            instance = super().__new__(cls, value)
            instance.__doc__ = doc
            return instance

    _DocumentedSubclass.__name__ = f'_Documented{base_type.__name__.capitalize()}'
    _DocumentedSubclass.__qualname__ = f'_Documented{base_type.__name__.capitalize()}'
    return _DocumentedSubclass

_DocumentedStr = _Documented(str)
_DocumentedTuple = _Documented(tuple)
