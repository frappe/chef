# verify_base.py — assert every one of the 9 first-party Frappe apps is INSTALLED (its schema
# was materialized), DISABLED (in the site's disabled_apps global), NOT ACTIVE (code inert),
# and that its DocType tables physically exist in the DB. Run inside the bench env from the
# bench `sites/` dir: `env/bin/python verify_base.py <site>`. Exits non-zero on any failure,
# which aborts the chef bake before the snapshot is taken.

import sys

import frappe

APPS = ["erpnext", "hrms", "lending", "crm", "helpdesk", "lms", "gameplan", "builder", "insights"]


def main() -> int:
    site = sys.argv[1]
    frappe.init(site)
    frappe.connect()

    installed = set(frappe.get_installed_apps())
    disabled = set(frappe.get_disabled_apps())
    active = set(frappe.get_active_apps())

    failures: list[str] = []
    for app in APPS:
        if app not in installed:
            failures.append(f"{app}: not in installed_apps (schema never materialized)")
            continue
        if app not in disabled:
            failures.append(f"{app}: not in disabled_apps (still enabled)")
        if app in active:
            failures.append(f"{app}: still in active_apps (code not inert)")

        # schema present: the app's DocTypes still have rows AND at least one table exists.
        modules = frappe.get_module_list(app)
        doctypes = (
            frappe.db.get_all(
                "DocType",
                {"module": ["in", modules], "issingle": 0, "is_virtual": 0},
                pluck="name",
            )
            if modules
            else []
        )
        if not doctypes:
            failures.append(f"{app}: no DocType rows (schema missing)")
            continue
        if not any(frappe.db.table_exists(dt) for dt in doctypes):
            failures.append(f"{app}: none of {len(doctypes)} doctype tables exist (schema missing)")

    if failures:
        print("VERIFY_FAIL")
        for line in failures:
            print("  -", line)
        return 1

    print(f"SUITE_OK: all {len(APPS)} apps installed + disabled with schema present")
    for app in APPS:
        print(f"  {app}: installed=yes disabled=yes active=no schema=present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
