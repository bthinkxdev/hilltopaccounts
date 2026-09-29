from apps.shared.exceptions import DomainError

class InvalidHandoverPayment(DomainError):
    pass

class HandoverNotSubmitted(DomainError):
    pass

class SelfConfirmationNotAllowed(DomainError):
    pass
