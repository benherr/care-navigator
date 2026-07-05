import re
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("CareNavigatorServer")

@mcp.tool()
def lookup_benefit_rules(program_name: str) -> str:
    """Lookup eligibility rules and requirements for a benefit program.
    
    Args:
        program_name: The name of the program (Medicaid, SNAP, LIHEAP).
    """
    program = program_name.upper()
    if "MEDICAID" in program:
        return (
            "Medicaid Eligibility Rules:\n"
            "- Available to low-income individuals, families, seniors, and people with disabilities.\n"
            "- Must be a resident of the state in which you apply.\n"
            "- U.S. citizenship or lawful permanent residency (5+ years) required.\n"
            "- Household income must be below the state threshold (usually 138% of the Federal Poverty Level)."
        )
    elif "SNAP" in program or "FOOD STAMP" in program:
        return (
            "SNAP (Supplemental Nutrition Assistance Program) Eligibility Rules:\n"
            "- Household must meet gross and net income limits.\n"
            "- Gross income limit: 130% of the Federal Poverty Level.\n"
            "- Net income limit: 100% of the Federal Poverty Level.\n"
            "- Resource limits apply (generally up to $3,000 in countable assets, excluding primary home)."
        )
    elif "LIHEAP" in program or "ENERGY ASSISTANCE" in program:
        return (
            "LIHEAP (Low Income Home Energy Assistance Program) Eligibility Rules:\n"
            "- Must need financial help to meet home energy costs.\n"
            "- Household income limit: generally 150% of the Federal Poverty Level or 60% of State Median Income."
        )
    return f"Program '{program_name}' not found. Available programs: Medicaid, SNAP, LIHEAP."

@mcp.tool()
def check_income_threshold(household_size: int, monthly_income: float) -> str:
    """Evaluates whether a household meets gross income limits for Medicaid, SNAP, and LIHEAP.
    
    Args:
        household_size: Number of people in the household.
        monthly_income: Total gross monthly income of the household.
    """
    # 2026 Federal Poverty Guidelines (Monthly approximate)
    # Base: $1,250 for 1 person, +$450 for each additional person
    fpl_base = 1250.0 + (household_size - 1) * 450.0
    
    # Thresholds:
    # Medicaid: 138% of FPL
    medicaid_threshold = fpl_base * 1.38
    # SNAP: 130% of FPL
    snap_threshold = fpl_base * 1.30
    # LIHEAP: 150% of FPL
    liheap_threshold = fpl_base * 1.50
    
    medicaid_eligible = monthly_income <= medicaid_threshold
    snap_eligible = monthly_income <= snap_threshold
    liheap_eligible = monthly_income <= liheap_threshold
    
    return (
        f"Income Threshold Assessment (Household Size: {household_size}, Monthly Income: ${monthly_income:.2f}):\n"
        f"- Federal Poverty Level (100%): ${fpl_base:.2f}\n"
        f"- Medicaid (138% FPL Limit: ${medicaid_threshold:.2f}) -> {'QUALIFIED' if medicaid_eligible else 'NOT QUALIFIED'}\n"
        f"- SNAP (130% FPL Limit: ${snap_threshold:.2f}) -> {'QUALIFIED' if snap_eligible else 'NOT QUALIFIED'}\n"
        f"- LIHEAP (150% FPL Limit: ${liheap_threshold:.2f}) -> {'QUALIFIED' if liheap_eligible else 'NOT QUALIFIED'}"
    )

@mcp.tool()
def search_local_offices(zip_code: str, program_name: str) -> str:
    """Finds local administration offices and application drop-off centers for a given zip code.
    
    Args:
        zip_code: The 5-digit zip code of the user.
        program_name: The benefit program name.
    """
    # Simulated search database based on zip code prefix
    prefix = zip_code[:3]
    if not re.match(r'^\d{5}$', zip_code):
        return "Invalid zip code format. Please provide a 5-digit zip code."
        
    return (
        f"Local Offices near Zip Code {zip_code} for {program_name}:\n"
        f"1. Department of Social Services (DSS) Office\n"
        f"   Address: {prefix} Main Street, Suite {zip_code[-2:]}, Local City\n"
        f"   Phone: (555) {prefix}-{zip_code[-4:]}\n"
        f"   Hours: Monday - Friday, 8:00 AM - 4:30 PM\n"
        f"   Services: In-person applications, document scanning, and case reviews.\n\n"
        f"2. Community Action Agency (LIHEAP partner)\n"
        f"   Address: 456 Energy Way, Local City\n"
        f"   Phone: (555) 999-8888\n"
        f"   Services: Energy assistance utility bill sign-ups."
    )

if __name__ == "__main__":
    mcp.run()
