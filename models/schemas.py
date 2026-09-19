from pydantic import BaseModel
from typing import Optional, List

class FormLead(BaseModel):
    first_name: str
    last_name: str
    phone: str
    email: str
    company: Optional[str] = None
    gclid: Optional[str] = None
    fbclid: Optional[str] = None
    li_fat_id: Optional[str] = None
    msclkid: Optional[str] = None
    ttclid: Optional[str] = None
    twclid: Optional[str] = None
    pin_clid: Optional[str] = None
    scclid: Optional[str] = None
    gptclid: Optional[str] = None
    rdt_cid: Optional[str] = None

class ExcludedCustomer(BaseModel):
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    email: Optional[str] = ""
    phone: Optional[str] = ""
    company_name: Optional[str] = ""

class UserInvite(BaseModel):
    email: str
    role: str
    client_id: Optional[int] = None

class UserRoleUpdate(BaseModel):
    email: str
    role: str

class UserDelete(BaseModel):
    email: str

class InviteRoleUpdate(BaseModel):
    token: str
    role: str

class InviteDelete(BaseModel):
    token: str

class SaleAdjustment(BaseModel):
    session_id: int
    adjustment_type: str # 'RETRACT' or 'RESTATE'
    adjusted_value: Optional[float] = 0.0

class ClientCreate(BaseModel):
    name: str
    call_tracking_provider: Optional[str] = "callrail"
    callrail_account_id: Optional[str] = ""
    callrail_company_id: Optional[str] = ""
    ctm_account_id: Optional[str] = ""
    ctm_profile_id: Optional[str] = ""
    wc_account_id: Optional[str] = ""
    wc_profile_id: Optional[str] = ""
    google_ads_customer_id: Optional[str] = ""
    facebook_ads_id: Optional[str] = ""
    tiktok_ads_id: Optional[str] = ""
    twitter_ads_id: Optional[str] = ""
    pinterest_ads_id: Optional[str] = ""
    snapchat_ads_id: Optional[str] = ""
    chatgpt_ads_id: Optional[str] = ""
    reddit_ads_id: Optional[str] = ""
    linkedin_ads_id: Optional[str] = ""
    microsoft_ads_id: Optional[str] = ""
    lead_gen_method: str
    qualification_criteria: str
    source_of_truth: str
    email_provider: Optional[str] = ""
    email_account: Optional[str] = ""
    email_app_password: Optional[str] = ""
    crm_deal_tags: Optional[str] = ""
    crm_won_deal_tags: Optional[str] = ""
    crm_value_field: Optional[str] = ""
    crm_lead_tags: Optional[str] = ""
    lead_count_rule: str
    exclude_past_customers: str

class ClientUpdate(BaseModel):
    id: int
    name: str
    call_tracking_provider: Optional[str] = "callrail"
    callrail_account_id: Optional[str] = ""
    callrail_company_id: Optional[str] = ""
    ctm_account_id: Optional[str] = ""
    ctm_profile_id: Optional[str] = ""
    wc_account_id: Optional[str] = ""
    wc_profile_id: Optional[str] = ""
    google_ads_customer_id: Optional[str] = ""
    facebook_ads_id: Optional[str] = ""
    tiktok_ads_id: Optional[str] = ""
    twitter_ads_id: Optional[str] = ""
    pinterest_ads_id: Optional[str] = ""
    snapchat_ads_id: Optional[str] = ""
    chatgpt_ads_id: Optional[str] = ""
    reddit_ads_id: Optional[str] = ""
    linkedin_ads_id: Optional[str] = ""
    microsoft_ads_id: Optional[str] = ""
    lead_gen_method: str
    qualification_criteria: str
    source_of_truth: str
    email_provider: Optional[str] = ""
    email_account: Optional[str] = ""
    email_app_password: Optional[str] = ""
    email_account_2: Optional[str] = ""
    email_app_password_2: Optional[str] = ""
    email_account_3: Optional[str] = ""
    email_app_password_3: Optional[str] = ""
    email_account_4: Optional[str] = ""
    email_app_password_4: Optional[str] = ""
    email_account_5: Optional[str] = ""
    email_app_password_5: Optional[str] = ""
    crm_deal_tags: Optional[str] = ""
    crm_won_deal_tags: Optional[str] = ""
    crm_value_field: Optional[str] = ""
    crm_lead_tags: Optional[str] = ""
    lead_count_rule: str
    exclude_past_customers: str
    excluded_customers: Optional[List[ExcludedCustomer]] = None
    exclusion_action: Optional[str] = "append"
