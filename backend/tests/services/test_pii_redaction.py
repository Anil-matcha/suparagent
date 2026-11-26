"""redact_pii (BL-174): what is stripped before a lesson is kept, and, asserted so the limit is visible, what is not."""
from app.services.history_learning import redact_pii


def test_emails_phones_and_links_with_a_query_are_replaced():
    out = redact_pii("Write to bob@example.com or call +1 (415) 555-0199. See https://shop.example.com/o?token=abc123 now.")
    assert "bob@example.com" not in out and "555-0199" not in out and "token=abc123" not in out
    assert "[email]" in out and "[number]" in out and "[link]" in out


def test_card_numbers_need_a_luhn_pass_and_order_ids_are_replaced():
    assert redact_pii("card 4111 1111 1111 1111 please") == "card [card] please"
    assert "[card]" not in redact_pii("number 4111 1111 1111 1112 here")   # fails the check, still gone as a long digit run
    assert "4111" not in redact_pii("number 4111 1111 1111 1112 here")
    assert redact_pii("ticket AB1234 closed") == "ticket [order] closed"
    assert "123456" not in redact_pii("order ORD-123456 shipped")


def test_thread_customer_names_are_replaced_everywhere():
    out = redact_pii("Hi Cora, thanks Cora Customer. Cora's refund is on its way.", names=("Cora Customer",))
    assert "Cora" not in out and out.count("[customer]") >= 3
    # a name that is not in the headers is not touched
    assert "Dana" in redact_pii("Dana will call back", names=("Cora Customer",))


def test_secrets_are_still_redacted():
    out = redact_pii("use password: hunter2hunter2 and token sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")
    assert "hunter2hunter2" not in out and "abcdefghijklmnopqrstuvwxyz0123456789" not in out


def test_known_limits_documented_cases_survive():
    """Free-text names and street addresses are NOT caught: the owner's review is the safeguard."""
    out = redact_pii("Tell your husband Raj to call 12 Baker Street, flat 4")
    assert "Raj" in out and "Baker Street" in out


def test_plain_dates_and_short_numbers_stay():
    assert redact_pii("On 2026-11-20 we shipped 3 items in 2 boxes.") == "On 2026-11-20 we shipped 3 items in 2 boxes."
