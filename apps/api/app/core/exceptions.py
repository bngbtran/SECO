class SECOError(Exception):
    """Base application error."""


class ResourceNotFoundError(SECOError):
    pass


class ConflictError(SECOError):
    pass


class BusinessRuleError(SECOError):
    pass
