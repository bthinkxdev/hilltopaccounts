from apps.shared.exceptions import DomainError

class PartitionVacant(DomainError):
    pass

class DuplicateInvoicePeriod(DomainError):
    pass

class InvoiceCancelled(DomainError):
    pass

class PaymentExceedsOutstanding(DomainError):
    pass

class NothingToCollect(DomainError):
    pass

class NothingToBill(DomainError):
    pass

class InvoiceHasPayments(DomainError):
    pass

class PaymentInHandover(DomainError):
    pass
