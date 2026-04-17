import csv
import io
import os
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import quote_plus

import bleach
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from database import (
    add_waitlist_registrant,
    authenticate_admin_user,
    create_admin_session,
    delete_admin_session,
    ensure_admin_role_and_user,
    get_all_registrants_for_csv,
    get_dashboard_stats,
    get_distribution_by_field,
    get_registrants_paginated,
    get_registrant_by_email,
    get_registrations_per_day,
    get_waitlist_count,
    init_db,
    is_admin_session_valid,
    prune_expired_admin_sessions,
    record_page_visit,
)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set in .env")


def get_non_negative_int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


PUBLIC_WAITLIST_BASE_COUNT = get_non_negative_int_from_env("PUBLIC_WAITLIST_BASE_COUNT", 1384)

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}$")

ROLE_OPTIONS = [
    "Founder / CEO",
    "Marketing Manager",
    "Sales Professional",
    "Developer / Engineer",
    "Data Analyst",
    "Legal Professional",
    "Finance / Accounting",
    "HR Professional",
    "Healthcare Professional",
    "Researcher / Academic",
    "Product Manager",
    "Consultant",
    "Operations Manager",
    "Other",
]

INDUSTRY_OPTIONS = [
    "Technology / Software",
    "Finance & Banking",
    "Healthcare & Medicine",
    "Legal",
    "Education",
    "E-Commerce & Retail",
    "Manufacturing",
    "Marketing & Advertising",
    "Real Estate",
    "Government & Public Sector",
    "Media & Entertainment",
    "Travel & Hospitality",
    "Food & Restaurant",
    "Agriculture",
    "Logistics & Transportation",
    "Insurance",
    "Telecommunications",
    "Energy & Utilities",
    "Nonprofit",
    "Research & Academia",
    "Other",
]

TICKER_ITEMS = [
    "Research & Academia",
    "Finance & Banking",
    "Legal",
    "Healthcare",
    "E-Commerce & Retail",
    "Manufacturing",
    "Education",
    "Government",
    "Marketing & Advertising",
    "IT & Software",
    "Real Estate",
    "Travel & Hospitality",
]

SERVICES_CATALOG = [
    {
        "id": 1,
        "icon": "bi bi-globe2",
        "name": "Web Browsing & Research",
        "services": [
            "Deep Multi-Source Research Synthesis",
            "Real-Time Product Price Comparison",
            "Academic Literature Search and Summarization",
            "Company Background Research",
            "Regulatory and Compliance Research",
            "Technology Stack Discovery",
            "Wikipedia Research and Cross-Referencing",
            "Forum and Community Sentiment Extraction",
            "Job Market Research",
            "Real Estate Listing Research",
            "Event and Conference Research",
            "News Aggregation and Briefing Generation",
            "Social Proof and Review Research",
            "Patent Research",
            "Grant and Funding Opportunity Research",
        ],
    },
    {
        "id": 2,
        "icon": "bi bi-file-earmark-text",
        "name": "File Creation & Management",
        "services": [
            "Multi-Section Report Generation",
            "Template Population from Data",
            "Bulk File Renaming and Organization",
            "Document Summarization",
            "Resume and Cover Letter Generation",
            "Contract Redlining and Markup Drafting",
            "Code File Documentation Generation",
            "Presentation Outline and Slide Script Creation",
            "Policy and Procedure Document Drafting",
            "Data-Driven Narrative Writing",
        ],
    },
    {
        "id": 3,
        "icon": "bi bi-envelope",
        "name": "Email Automation",
        "services": [
            "Inbox Triage and Priority Classification",
            "Personalized Cold Email Drafting at Scale",
            "Email Thread Summarization",
            "Meeting Follow-Up Email Drafting",
            "Customer Complaint Response Drafting",
            "Email Campaign Copy Writing",
            "Vendor and Supplier Correspondence",
            "Legal Notice and Demand Letter Drafting",
            "Unsubscribe and Inbox Cleaning Assistance",
            "Proposal and Quote Email Writing",
        ],
    },
    {
        "id": 4,
        "icon": "bi bi-calendar3",
        "name": "Calendar & Scheduling",
        "services": [
            "Meeting Scheduling from Availability Context",
            "Calendar Event Creation from Email or Notes",
            "Weekly Agenda Preparation",
            "Recurring Event and Deadline Tracking",
            "Interview Scheduling Coordination",
        ],
    },
    {
        "id": 5,
        "icon": "bi bi-ui-checks-grid",
        "name": "Form Filling & Data Entry",
        "services": [
            "Web Form Auto-Population",
            "Government Form Assistance and Population",
            "CRM Data Entry from Meeting Notes",
            "Insurance Claims Data Preparation",
            "Survey Response Data Consolidation",
            "E-commerce Order Form Completion",
        ],
    },
    {
        "id": 6,
        "icon": "bi bi-code-slash",
        "name": "Code Writing & Debugging",
        "services": [
            "Full Application Code Generation",
            "Bug Identification and Fix Generation",
            "Code Refactoring for Readability and Performance",
            "Unit Test Generation",
            "SQL Query Writing and Optimization",
            "API Integration Code Writing",
            "Shell Script and Automation Script Generation",
            "Data Analysis Script Writing",
            "Code Explanation and Documentation",
            "Infrastructure-as-Code Generation",
            "Regex Pattern Generation",
            "Code Migration Between Languages or Frameworks",
            "Security Vulnerability Review",
            "Environment Setup and Dependency Configuration",
            "Algorithm Design and Implementation",
        ],
    },
    {
        "id": 7,
        "icon": "bi bi-bug",
        "name": "Data Scraping & Extraction",
        "services": [
            "Product Listing Extraction",
            "Contact Information Extraction from Directories",
            "News Article Extraction and Structuring",
            "Job Posting Extraction and Aggregation",
            "Financial Data Extraction from Reports",
            "Real Estate Data Extraction",
            "Review and Rating Extraction",
            "Scholarly Citation Extraction",
            "Government Data Extraction",
            "Event and Agenda Data Extraction",
        ],
    },
    {
        "id": 8,
        "icon": "bi bi-table",
        "name": "Spreadsheet & Document Automation",
        "services": [
            "Financial Model Building",
            "Dashboard Creation from Raw Data",
            "Data Cleaning and Deduplication",
            "Formula Audit and Error Correction",
            "Contract and Document Comparison",
            "Mail Merge Document Generation",
            "Report Compilation from Multiple Sources",
            "Budget vs. Actuals Analysis",
        ],
    },
    {
        "id": 9,
        "icon": "bi bi-plug",
        "name": "API Calls & Integrations",
        "services": [
            "REST API Data Retrieval and Processing",
            "Webhook Configuration Assistance",
            "GraphQL Query Writing",
            "Authentication Flow Implementation",
            "API Response Schema Documentation",
            "Third-Party Service Configuration Code",
            "Data Transformation Between API Formats",
        ],
    },
    {
        "id": 10,
        "icon": "bi bi-cart3",
        "name": "Shopping & E-Commerce",
        "services": [
            "Multi-Site Price Tracking and Alerting Logic",
            "Product Availability Checking",
            "Coupon and Promo Code Research",
            "Product Specification Comparison",
            "Wholesale Supplier Discovery",
            "Amazon Product Research",
            "Subscription Management Research",
        ],
    },
    {
        "id": 11,
        "icon": "bi bi-chat-square-dots",
        "name": "Social Media Automation",
        "services": [
            "Social Media Content Calendar Creation",
            "Post Copy Writing for Multiple Platforms",
            "Competitor Social Media Analysis",
            "Hashtag Research",
            "Influencer Research and List Building",
            "Community Post and Response Drafting",
            "Social Listening Summary",
        ],
    },
    {
        "id": 12,
        "icon": "bi bi-newspaper",
        "name": "News & Content Aggregation",
        "services": [
            "Industry Newsletter Compilation",
            "Earnings and Financial News Aggregation",
            "Regulatory Filing and Announcement Monitoring",
            "Research Paper Alert Aggregation",
            "Competitor Blog and Content Monitoring",
            "Social News Trend Identification",
        ],
    },
    {
        "id": 13,
        "icon": "bi bi-airplane",
        "name": "Travel Booking & Research",
        "services": [
            "Flight Option Research",
            "Hotel Research and Comparison",
            "Visa and Entry Requirement Research",
            "Travel Itinerary Building",
            "Travel Insurance Research",
            "Ground Transportation Research",
            "Currency and Cost-of-Living Research",
        ],
    },
    {
        "id": 14,
        "icon": "bi bi-briefcase",
        "name": "Job Application Automation",
        "services": [
            "Job Description Analysis and Matching",
            "Resume Customization Per Role",
            "Cover Letter Generation",
            "LinkedIn Profile Optimization",
            "Application Tracking Sheet Creation",
            "Interview Preparation Brief",
            "Salary Research and Negotiation Brief",
        ],
    },
    {
        "id": 15,
        "icon": "bi bi-headset",
        "name": "Customer Support Automation",
        "services": [
            "FAQ Knowledge Base Drafting",
            "Support Ticket Classification and Routing",
            "Canned Response Library Creation",
            "Escalation Summary Drafting",
            "Customer Satisfaction Survey Analysis",
            "Help Center Article Writing",
            "Chatbot Script and Flow Writing",
        ],
    },
    {
        "id": 16,
        "icon": "bi bi-currency-dollar",
        "name": "Financial Data Research",
        "services": [
            "Equity Research Report Drafting",
            "Financial Statement Analysis",
            "Macro-Economic Data Research",
            "Crypto and DeFi Market Research",
            "Tax Law Research",
            "Budget Variance Report Writing",
            "M&A Target Research",
        ],
    },
    {
        "id": 17,
        "icon": "bi bi-bank",
        "name": "Legal Document Drafting",
        "services": [
            "NDA Drafting",
            "Service Agreement Drafting",
            "Terms of Service and Privacy Policy Drafting",
            "Employment Offer Letter Drafting",
            "Cease and Desist Letter Drafting",
            "Partnership Agreement Outline",
            "Demand Letter for Unpaid Invoices",
            "GDPR Data Subject Request Response Drafting",
        ],
    },
    {
        "id": 18,
        "icon": "bi bi-heart-pulse",
        "name": "Medical Research",
        "services": [
            "Clinical Trial Research",
            "Drug Interaction Research",
            "Medical Literature Summarization",
            "Symptom and Differential Diagnosis Research",
            "Treatment Guideline Research",
            "Health Insurance Coverage Research",
            "Medical Device Regulatory Research",
        ],
    },
    {
        "id": 19,
        "icon": "bi bi-mortarboard",
        "name": "Education & Tutoring",
        "services": [
            "Personalized Study Plan Generation",
            "Practice Problem Generation",
            "Essay Feedback and Grading",
            "Concept Explanation at Multiple Levels",
            "Reading Comprehension Question Generation",
            "Vocabulary and Definition Research",
            "Research Paper Outline and Draft",
            "Lesson Plan Generation",
        ],
    },
    {
        "id": 20,
        "icon": "bi bi-image",
        "name": "Image & Media Research",
        "services": [
            "Stock Image Search and Curation",
            "Brand Asset Competitive Analysis",
            "Icon and Illustration Library Research",
            "Video Content Research",
            "Image Metadata and Attribution Research",
        ],
    },
    {
        "id": 21,
        "icon": "bi bi-database",
        "name": "Database Querying & Management",
        "services": [
            "Database Schema Design",
            "SQL Query Generation for Analytics",
            "Data Migration Script Writing",
            "Index Optimization Recommendation",
            "ETL Pipeline Code Writing",
            "NoSQL Data Model Design",
        ],
    },
    {
        "id": 22,
        "icon": "bi bi-hdd-network",
        "name": "IT & System Administration",
        "services": [
            "Infrastructure Documentation Writing",
            "Network Diagram Description and Design",
            "Log Analysis and Error Diagnosis",
            "Security Policy Drafting",
            "CI/CD Pipeline Configuration",
            "Cloud Cost Optimization Research",
            "Container and Orchestration Configuration",
        ],
    },
    {
        "id": 23,
        "icon": "bi bi-kanban",
        "name": "Project Management Automation",
        "services": [
            "Project Plan Generation",
            "Risk Register Creation",
            "Status Report Writing",
            "Meeting Agenda Preparation",
            "RACI Matrix Creation",
            "Retrospective Facilitation Framework",
        ],
    },
    {
        "id": 24,
        "icon": "bi bi-people",
        "name": "CRM & Sales Automation",
        "services": [
            "Lead Scoring Framework Development",
            "Sales Sequence Script Writing",
            "Account Research Briefing",
            "Competitive Battle Card Creation",
            "Sales Email Personalization at Scale",
            "CRM Data Enrichment",
            "Pipeline Review Report",
        ],
    },
    {
        "id": 25,
        "icon": "bi bi-person-workspace",
        "name": "HR & Recruitment Automation",
        "services": [
            "Job Description Writing",
            "Interview Question Bank Creation",
            "Candidate Screening Criteria Development",
            "Onboarding Document Package",
            "Performance Review Template Creation",
            "Employee Survey Design",
            "Compensation Benchmarking Research",
        ],
    },
    {
        "id": 26,
        "icon": "bi bi-graph-up-arrow",
        "name": "Marketing & SEO",
        "services": [
            "Keyword Research and Clustering",
            "SEO-Optimized Blog Post Writing",
            "Technical SEO Audit Preparation",
            "Competitor Content Gap Analysis",
            "Ad Copy Writing",
            "Landing Page Copy Writing",
            "Email Marketing A/B Test Variant Creation",
            "Marketing Brief Writing",
            "Content Repurposing Across Formats",
            "Influencer Outreach Email Writing",
        ],
    },
    {
        "id": 27,
        "icon": "bi bi-diagram-3",
        "name": "Multi-Step Complex Workflows",
        "services": [
            "End-to-End Lead Research and Outreach Workflow",
            "Competitive Intelligence Report Production",
            "Research to Writing to Publish Workflow",
            "RFP Response Assembly",
            "Due Diligence Checklist Completion",
        ],
    },
    {
        "id": 28,
        "icon": "bi bi-house-door",
        "name": "Real Estate Research & Automation",
        "services": [
            "Comparable Sales Analysis",
            "Rental Market Research",
            "Neighborhood Research Report",
            "Zoning and Permit Research",
            "HOA Research",
        ],
    },
    {
        "id": 29,
        "icon": "bi bi-truck",
        "name": "Supply Chain & Logistics Research",
        "services": [
            "Supplier Qualification Research",
            "Shipping Rate Comparison",
            "Import/Export Regulation Research",
            "Logistics Technology Vendor Research",
        ],
    },
    {
        "id": 30,
        "icon": "bi bi-building",
        "name": "Government & Compliance Form Automation",
        "services": [
            "Business License Application Assistance",
            "Tax Form Preparation Support",
            "Compliance Calendar Building",
            "Grant Application Drafting",
            "Regulatory Comment Letter Drafting",
        ],
    },
    {
        "id": 31,
        "icon": "bi bi-stars",
        "name": "Additional Real-World Tasks",
        "services": [
            "Personal Finance Research and Budgeting",
            "Recipe Scaling and Meal Planning",
            "Book and Movie Research and Recommendation",
            "Event Planning Research",
            "Language Learning Material Creation",
            "Podcast and Video Content Research",
            "Nonprofit Impact Report Drafting",
            "Scientific Experiment Design Assistance",
            "SOP Standard Operating Procedure Writing",
            "Franchise Research",
        ],
    },
    {
        "id": 32,
        "icon": "bi bi-piggy-bank",
        "name": "Finance & Banking",
        "services": [
            "F1 Automated Account Reconciliation",
            "F2 Automated Invoice Processing and AP",
            "F3 Automated Accounts Receivable and Collections",
            "F4 Loan Origination Processing Automation",
            "F5 Regulatory Reporting Automation CCAR DFAST Basel III",
            "F6 Anti-Money Laundering AML Transaction Monitoring",
            "F7 KYC Document Verification",
            "F8 Trade Settlement Automation",
            "F9 Algorithmic Trading Execution",
            "F10 Automated Financial Close Process",
            "F11 Expense Report Processing",
            "F12 Credit Scoring Model Execution",
            "F13 Portfolio Rebalancing Automation",
            "F14 Bank Statement Data Extraction",
            "F15 Treasury Cash Position Reporting",
            "F16 Fraud Detection and Card Alert Automation",
            "F17 Tax Calculation and Filing Automation",
            "F18 Payroll Processing Automation",
            "F19 Insurance Premium Calculation",
            "F20 Audit Trail and Transaction Log Automation",
        ],
    },
    {
        "id": 33,
        "icon": "bi bi-hospital",
        "name": "Healthcare & Medicine",
        "services": [
            "H1 EHR Data Entry Automation",
            "H2 Prior Authorization Automation",
            "H3 Claims Processing and Adjudication",
            "H4 Appointment Scheduling and Reminder Automation",
            "H5 Lab Result Distribution Automation",
            "H6 Medication Dispensing Automation",
            "H7 Patient Discharge Summary Generation",
            "H8 Population Health Management Outreach",
            "H9 Medical Coding Automation ICD-10 CPT",
            "H10 Drug Interaction and Allergy Checking",
            "H11 Clinical Trial Eligibility Screening",
            "H12 Radiology Report Generation Assistance",
            "H13 Revenue Cycle Denial Management",
            "H14 Patient Intake Form Automation",
            "H15 HIPAA Compliance Monitoring",
            "H16 Pharmacy Benefit Management PBM Processing",
            "H17 Surgical Case Scheduling and Resource Allocation",
        ],
    },
    {
        "id": 34,
        "icon": "bi bi-shield-check",
        "name": "Legal",
        "services": [
            "LE1 Contract Analysis and Risk Flagging",
            "LE2 Contract Lifecycle Management CLM",
            "LE3 Legal Document Review eDiscovery",
            "LE4 Legal Research Automation",
            "LE5 Regulatory Change Monitoring",
            "LE6 IP Portfolio Management Automation",
            "LE7 Due Diligence Data Room Processing",
            "LE8 Billing and Time Entry Automation",
            "LE9 Pleading and Motion Drafting Assistance",
            "LE10 Court Deadline and Docket Management",
            "LE11 Compliance Document Generation",
            "LE12 E-Signature Workflow Automation",
        ],
    },
    {
        "id": 35,
        "icon": "bi bi-book",
        "name": "Education Enterprise",
        "services": [
            "ED1 Student Information System SIS Data Management",
            "ED2 LMS Content Deployment",
            "ED3 Automated Grading for Objective Assessments",
            "ED4 Plagiarism Detection",
            "ED5 Student Communication Automation",
            "ED6 Admissions Application Processing",
            "ED7 Adaptive Learning Content Delivery",
            "ED8 Financial Aid Processing",
            "ED9 Attendance Tracking and Reporting",
            "ED10 Course Recommendation Generation",
            "ED11 Credential and Certificate Issuance",
            "ED12 Library System Automation",
        ],
    },
    {
        "id": 36,
        "icon": "bi bi-bag",
        "name": "E-Commerce & Retail",
        "services": [
            "EC1 Order Management and Fulfillment Automation",
            "EC2 Inventory Level Monitoring and Reorder Automation",
            "EC3 Dynamic Pricing Automation",
            "EC4 Product Catalog Data Enrichment",
            "EC5 Customer Segmentation and Targeting",
            "EC6 Cart Abandonment Recovery Automation",
            "EC7 Returns and Refunds Processing",
            "EC8 Fraud Detection and Order Risk Scoring",
            "EC9 Personalized Product Recommendation Engine",
            "EC10 Supplier EDI Integration and PO Processing",
            "EC11 Loyalty Program Management",
            "EC12 Review and UGC Collection Automation",
            "EC13 Marketplace Listing Syndication",
            "EC14 Tax Calculation at Checkout",
        ],
    },
    {
        "id": 37,
        "icon": "bi bi-gear-wide-connected",
        "name": "Manufacturing & Supply Chain",
        "services": [
            "M1 ERP Production Order Automation",
            "M2 MRP Run Automation",
            "M3 Quality Control Inspection Data Collection",
            "M4 Predictive Maintenance Scheduling",
            "M5 Shop Floor Data Collection SFDC",
            "M6 Supplier Performance Monitoring",
            "M7 Demand Forecasting Automation",
            "M8 Bill of Materials BOM Management",
            "M9 Warehouse Management System WMS Automation",
            "M10 Automated Guided Vehicle AGV Coordination",
            "M11 Dispatch and Route Optimization",
            "M12 EDI Order Processing Automation",
            "M13 ISO and Regulatory Document Control",
        ],
    },
    {
        "id": 38,
        "icon": "bi bi-heart",
        "name": "Nonprofit",
        "services": [
            "NP1 Donor Management and Giving History Tracking",
            "NP2 Online Fundraising Campaign Automation",
            "NP3 Grant Reporting Automation",
            "NP4 Volunteer Management Automation",
            "NP5 Impact Metrics Collection and Reporting",
            "NP6 Membership Renewal Automation",
        ],
    },
    {
        "id": 39,
        "icon": "bi bi-search",
        "name": "Research & Academia",
        "services": [
            "RA1 Literature Review Automation",
            "RA2 Systematic Review and Meta-Analysis Support",
            "RA3 Research Data Management",
            "RA4 Lab Instrument Data Capture",
            "RA5 Statistical Analysis Pipeline Automation",
            "RA6 Academic Publishing Submission Tracking",
            "RA7 IRB Protocol Management",
            "RA8 Citation and Bibliography Management",
            "RA9 Research Grant Application Tracking",
        ],
    },
    {
        "id": 40,
        "icon": "bi bi-lightning-charge",
        "name": "Personal Productivity & Life Automation",
        "services": [
            "PP1 Email Inbox Automation and Filtering",
            "PP2 Personal Finance Aggregation and Tracking",
            "PP3 Bill Payment Automation",
            "PP4 Calendar and Task Integration",
            "PP5 Smart Home Automation",
            "PP6 Health and Fitness Tracking",
            "PP7 Subscription Tracking and Management",
            "PP8 Travel Itinerary Parsing and Organization",
            "PP9 Password and Credential Management",
            "PP10 News and Reading Digest Curation",
            "PP11 Social Media Archiving",
            "PP12 File Organization and Cloud Sync",
            "PP13 Shopping Price Drop Alert",
            "PP14 Meeting Transcription and Summary",
            "PP15 Backup Automation for Personal Files",
        ],
    },
    {
        "id": 41,
        "icon": "bi bi-buildings",
        "name": "Real Estate",
        "services": [
            "RE1 MLS Data Synchronization",
            "RE2 Lease Administration Automation",
            "RE3 Tenant Rent Collection and Late Fee Processing",
            "RE4 Maintenance Request Routing and Tracking",
            "RE5 Property Listing Syndication",
            "RE6 Tenant Screening Automation",
            "RE7 Property Tax Assessment Monitoring",
            "RE8 Appraisal Data Management",
            "RE9 Commercial Real Estate Lease Abstraction",
            "RE10 Investor Reporting Automation",
        ],
    },
    {
        "id": 42,
        "icon": "bi bi-people-fill",
        "name": "Human Resources",
        "services": [
            "HR1 Applicant Tracking System ATS Automation",
            "HR2 Resume Parsing and Screening",
            "HR3 Onboarding Workflow Automation",
            "HR4 Benefits Enrollment Processing",
            "HR5 Performance Management Cycle Automation",
            "HR6 Time and Attendance Tracking",
            "HR7 Leave Management Automation",
            "HR8 Employee Separation and Offboarding Automation",
            "HR9 Compensation Planning and Modeling",
            "HR10 Learning and Development Assignment Automation",
            "HR11 Employee Survey Distribution and Analysis",
            "HR12 Headcount and Workforce Planning Reporting",
        ],
    },
    {
        "id": 43,
        "icon": "bi bi-megaphone",
        "name": "Marketing & Advertising",
        "services": [
            "MA1 Marketing Automation Workflow Execution",
            "MA2 Lead Scoring and Lifecycle Stage Updates",
            "MA3 Ad Campaign Bid Management",
            "MA4 SEO Rank Tracking and Reporting",
            "MA5 Content Publishing Scheduling",
            "MA6 UTM Parameter Management and Attribution",
            "MA7 A/B Test Automation and Statistical Significance Monitoring",
            "MA8 Programmatic Ad Buying",
            "MA9 Marketing Performance Dashboard Automation",
            "MA10 Influencer Campaign Tracking",
            "MA11 Customer Journey Orchestration",
            "MA12 Social Listening and Sentiment Monitoring",
        ],
    },
    {
        "id": 44,
        "icon": "bi bi-cpu",
        "name": "IT & Software Development",
        "services": [
            "IT1 Infrastructure Provisioning IaC",
            "IT2 CI/CD Pipeline Execution",
            "IT3 Application Performance Monitoring and Alerting",
            "IT4 Incident Management and Runbook Automation",
            "IT5 Security Patch Management",
            "IT6 User Access Provisioning and Deprovisioning",
            "IT7 Backup and Disaster Recovery Automation",
            "IT8 Log Aggregation and SIEM Automation",
            "IT9 Software Testing Automation",
            "IT10 Container Orchestration and Auto-Scaling",
            "IT11 DNS and SSL Certificate Management",
            "IT12 Database Backup and Failover Automation",
            "IT13 ITSM Ticket Routing and Resolution",
            "IT14 Configuration Management and Drift Detection",
            "IT15 API Gateway Management and Rate Limiting",
        ],
    },
    {
        "id": 45,
        "icon": "bi bi-headset",
        "name": "Customer Service",
        "services": [
            "CS1 Chatbot Conversational AI Deployment",
            "CS2 Ticket Auto-Classification and Routing",
            "CS3 Suggested Response Generation",
            "CS4 SLA Monitoring and Escalation",
            "CS5 Customer Satisfaction Survey Triggering",
            "CS6 Knowledge Base Article Publishing Workflow",
            "CS7 IVR Interactive Voice Response Call Routing",
            "CS8 Agent Performance Scorecard Automation",
            "CS9 Order Status Inquiry Automation",
            "CS10 Proactive Service Alert Notifications",
        ],
    },
    {
        "id": 46,
        "icon": "bi bi-bank2",
        "name": "Government & Public Services",
        "services": [
            "G1 Benefits Eligibility Determination Automation",
            "G2 Permit Application Processing",
            "G3 Tax Assessment and Collection System Automation",
            "G4 311 Request Management",
            "G5 Court Case Management Automation",
            "G6 Election and Voter Registration Management",
            "G7 Grant Management and Compliance Reporting",
            "G8 FOIA Request Processing",
            "G9 Public Records and Document Management",
            "G10 Social Services Case Management",
        ],
    },
    {
        "id": 47,
        "icon": "bi bi-camera-reels",
        "name": "Media & Entertainment",
        "services": [
            "ME1 Content Metadata Management",
            "ME2 Video Transcription and Captioning",
            "ME3 Content Moderation Automation",
            "ME4 Royalty Calculation and Distribution",
            "ME5 Ad Insertion and DAI Dynamic Ad Insertion",
            "ME6 Content Delivery Network CDN Management",
            "ME7 Subtitle and Localization Workflow Automation",
            "ME8 Publishing Workflow Automation",
            "ME9 Programmatic Content Licensing",
        ],
    },
    {
        "id": 48,
        "icon": "bi bi-airplane-engines",
        "name": "Travel & Hospitality",
        "services": [
            "TH1 Central Reservation System CRS Management",
            "TH2 Revenue Management and Dynamic Pricing",
            "TH3 Guest Communication Automation",
            "TH4 GDS Content Management",
            "TH5 OTA Channel Management",
            "TH6 Housekeeping Workflow Management",
            "TH7 Food and Beverage Inventory Management",
            "TH8 Loyalty Program Management and Communication",
        ],
    },
    {
        "id": 49,
        "icon": "bi bi-cup-straw",
        "name": "Food & Restaurant Industry",
        "services": [
            "FR1 POS Data Reporting",
            "FR2 Menu Engineering Analysis",
            "FR3 Online Ordering and Delivery Integration",
            "FR4 Food Cost and Recipe Costing Automation",
            "FR5 Staff Scheduling Automation",
            "FR6 Vendor Invoice Processing",
            "FR7 Health Inspection Compliance Documentation",
        ],
    },
    {
        "id": 50,
        "icon": "bi bi-flower1",
        "name": "Agriculture",
        "services": [
            "AG1 Precision Agriculture Data Collection and Analysis",
            "AG2 Crop Yield Prediction Modeling",
            "AG3 Irrigation Scheduling Automation",
            "AG4 Livestock Monitoring and Health Alert",
            "AG5 Farm Management Information System FMIS Record-Keeping",
            "AG6 Commodity Price Monitoring and Alerting",
            "AG7 Agricultural Equipment Telematics",
        ],
    },
    {
        "id": 51,
        "icon": "bi bi-truck-flatbed",
        "name": "Logistics & Transportation",
        "services": [
            "LT1 Freight Quote Automation",
            "LT2 Shipment Tracking and Visibility",
            "LT3 Customs Documentation Automation",
            "LT4 Driver HOS Hours of Service Compliance",
            "LT5 Load Board and Carrier Matching",
            "LT6 Fleet Maintenance Scheduling",
            "LT7 Last-Mile Delivery Route Optimization",
            "LT8 Cross-Docking Coordination",
        ],
    },
    {
        "id": 52,
        "icon": "bi bi-shield-lock",
        "name": "Insurance",
        "services": [
            "IN1 First Notice of Loss FNOL Processing",
            "IN2 Automated Underwriting Decision",
            "IN3 Policy Issuance and Documentation",
            "IN4 Premium Billing and Collection",
            "IN5 Claims Reserve Calculation",
            "IN6 Fraud Analytics and Investigation Queuing",
            "IN7 Compliance Filing Automation",
            "IN8 Reinsurance Bordereau Generation",
        ],
    },
    {
        "id": 53,
        "icon": "bi bi-broadcast",
        "name": "Telecommunications",
        "services": [
            "TE1 Network Fault Detection and Auto-Healing",
            "TE2 Customer Account Provisioning",
            "TE3 Usage-Based Billing Automation",
            "TE4 Churn Prediction and Retention Outreach",
            "TE5 Network Capacity Planning Automation",
            "TE6 SIM Card Management and Activation",
        ],
    },
    {
        "id": 54,
        "icon": "bi bi-lightning",
        "name": "Energy & Utilities",
        "services": [
            "EU1 Smart Meter Data Collection and Billing",
            "EU2 SCADA and Energy Management System Automation",
            "EU3 Outage Management Automation",
            "EU4 Renewable Energy Forecasting",
            "EU5 Demand Response Automation",
            "EU6 Environmental Compliance Reporting",
            "EU7 Pipeline Monitoring and Leak Detection",
        ],
    },
]


def flatten_services() -> list[str]:
    output: list[str] = []
    for category in SERVICES_CATALOG:
        output.extend(category["services"])
    return output


ALL_SERVICES = flatten_services()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-to-a-random-64-char-string")
app.config["DATABASE_URL"] = DATABASE_URL
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

init_db(app.config["DATABASE_URL"])


def bootstrap_admin_identity() -> None:
    admin_email = os.getenv("ADMIN_EMAIL", "admin@avenor.ai").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD", "")

    if not EMAIL_REGEX.match(admin_email):
        raise RuntimeError("ADMIN_EMAIL must be a valid email address in .env")

    if not admin_password or admin_password == "change-this-to-a-strong-password":
        raise RuntimeError("Set a strong ADMIN_PASSWORD in .env before running the app")

    ensure_admin_role_and_user(
        admin_email=admin_email,
        admin_password=admin_password,
        db_path=app.config["DATABASE_URL"],
    )


bootstrap_admin_identity()


def get_client_ip() -> str | None:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


def anonymize_ip(ip_address: str | None) -> str | None:
    if not ip_address:
        return None
    if ":" in ip_address:
        parts = [p for p in ip_address.split(":") if p]
        if not parts:
            return "*"
        visible = parts[:4]
        return ":".join(visible) + ":*"
    parts = ip_address.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".*"
    return "*"


def sanitize_text(value: str | None, max_length: int = 255) -> str:
    cleaned = bleach.clean((value or "").strip(), strip=True)
    return cleaned[:max_length]


def normalize_optional_choice(value: str, allowed: list[str]) -> str | None:
    if not value:
        return None
    if value in allowed:
        return value
    return "Other" if "Other" in allowed else None


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))


def to_public_waitlist_number(value: int | None) -> int:
    safe_value = int(value or 0)
    return max(safe_value, 0) + PUBLIC_WAITLIST_BASE_COUNT


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        prune_expired_admin_sessions(app.config["DATABASE_URL"])
        session_token = session.get("admin_session_token")
        is_authed = session.get("admin_authenticated", False)
        if not is_authed or not session_token:
            return redirect(url_for("admin_login"))
        if not is_admin_session_valid(session_token, app.config["DATABASE_URL"]):
            session.clear()
            flash("Your admin session expired. Please log in again.", "warning")
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_global_context():
    return {
        "current_year": datetime.now().year,
        "total_service_count": len(ALL_SERVICES),
        "category_count": len(SERVICES_CATALOG),
    }


def process_waitlist_registration():
    first_name = sanitize_text(request.form.get("first_name"), max_length=50)
    last_name = sanitize_text(request.form.get("last_name"), max_length=50)
    email = sanitize_text(request.form.get("email"), max_length=254).lower()
    role_raw = sanitize_text(request.form.get("role"), max_length=100)
    industry_raw = sanitize_text(request.form.get("industry"), max_length=100)

    role = normalize_optional_choice(role_raw, ROLE_OPTIONS)
    industry = normalize_optional_choice(industry_raw, INDUSTRY_OPTIONS)

    errors: list[str] = []
    if not first_name or len(first_name) > 50:
        errors.append("Please enter a valid first name.")
    if not last_name or len(last_name) > 50:
        errors.append("Please enter a valid last name.")
    if not email or not is_valid_email(email):
        errors.append("Please enter a valid email address.")

    if errors:
        for error in errors:
            flash(error, "danger")
        return redirect(url_for("index", _anchor="join-waitlist"))

    client_ip = anonymize_ip(get_client_ip())
    user_agent = sanitize_text(request.headers.get("User-Agent"), max_length=500)
    referrer = sanitize_text(request.referrer, max_length=500)

    try:
        result = add_waitlist_registrant(
            first_name=first_name,
            last_name=last_name,
            email=email,
            role=role,
            industry=industry,
            ip_address=client_ip,
            user_agent=user_agent,
            referrer=referrer,
            db_path=app.config["DATABASE_URL"],
        )
    except Exception:
        flash("Something went wrong while saving your registration. Please try again.", "danger")
        return redirect(url_for("index", _anchor="join-waitlist"))

    if result["status"] == "duplicate":
        existing = get_registrant_by_email(email, app.config["DATABASE_URL"])
        raw_position = existing["position"] if existing else result.get("position", 0)
        session["registration_context"] = {
            "first_name": existing["first_name"] if existing else first_name,
            "position": to_public_waitlist_number(raw_position),
            "email": email,
        }
        flash("You are already on the list. Your spot is reserved.", "info")
        return redirect(url_for("success"))

    session["registration_context"] = {
        "first_name": result.get("first_name", first_name),
        "position": to_public_waitlist_number(result.get("position", 0)),
        "email": email,
    }
    flash("You are on the waitlist. Welcome aboard.", "success")
    return redirect(url_for("success"))


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        return process_waitlist_registration()

    try:
        record_page_visit(
            ip_address=anonymize_ip(get_client_ip()),
            user_agent=sanitize_text(request.headers.get("User-Agent"), max_length=500),
            referrer=sanitize_text(request.referrer, max_length=500),
            db_path=app.config["DATABASE_URL"],
        )
    except Exception:
        pass

    waitlist_count = to_public_waitlist_number(get_waitlist_count(app.config["DATABASE_URL"]))

    return render_template(
        "index.html",
        waitlist_count=waitlist_count,
        role_options=ROLE_OPTIONS,
        industry_options=INDUSTRY_OPTIONS,
        services_catalog=SERVICES_CATALOG,
        ticker_items=TICKER_ITEMS,
    )


@app.route("/register", methods=["GET", "POST"], strict_slashes=False)
def register():
    if request.method == "GET":
        return redirect(url_for("index", _anchor="join-waitlist"))
    return process_waitlist_registration()


@app.get("/success")
def success():
    registration_context = session.get("registration_context")
    if not registration_context:
        return redirect(url_for("index"))

    sample_count = min(12, len(ALL_SERVICES))
    services_preview = random.sample(ALL_SERVICES, sample_count)

    home_url = request.url_root.rstrip("/") + url_for("index")
    share_text = (
        "I just joined the Avenor waitlist and I am excited about a platform with "
        "481 AI automation use cases in one place. Join me"
    )
    x_share_link = f"https://twitter.com/intent/tweet?text={quote_plus(share_text)}&url={quote_plus(home_url)}"
    linkedin_share_link = (
        "https://www.linkedin.com/sharing/share-offsite/?url=" + quote_plus(home_url)
    )

    return render_template(
        "success.html",
        first_name=registration_context.get("first_name", "there"),
        position=registration_context.get("position", 0),
        services_preview=services_preview,
        home_url=home_url,
        x_share_link=x_share_link,
        linkedin_share_link=linkedin_share_link,
        share_text=share_text,
    )


@app.get("/api/waitlist-count")
def api_waitlist_count():
    count = to_public_waitlist_number(get_waitlist_count(app.config["DATABASE_URL"]))
    return jsonify({"count": count})


@app.get("/admin")
def admin_root():
    if session.get("admin_authenticated") and session.get("admin_session_token"):
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("admin_login"))


@app.get("/admin/login")
def admin_login():
    return render_template("admin_login.html")


@app.post("/admin/login")
def admin_login_post():
    email = sanitize_text(request.form.get("email"), max_length=254).lower()
    password = request.form.get("password", "")

    auth_user = authenticate_admin_user(
        admin_email=email,
        admin_password=password,
        db_path=app.config["DATABASE_URL"],
    )

    if auth_user:
        session.clear()
        session.permanent = True
        session["admin_authenticated"] = True
        session["admin_email"] = auth_user["email"]
        session["admin_role"] = auth_user["role"]

        token = secrets.token_urlsafe(32)
        session["admin_session_token"] = token
        expires_at = datetime.now(timezone.utc) + timedelta(hours=12)
        create_admin_session(token, expires_at, app.config["DATABASE_URL"])

        flash("Admin login successful.", "success")
        return redirect(url_for("admin_dashboard"))

    flash("Invalid admin credentials.", "danger")
    return redirect(url_for("admin_login"))


@app.get("/admin/dashboard")
@admin_required
def admin_dashboard():
    sort_by = request.args.get("sort", "date")
    page_raw = request.args.get("page", "1")
    try:
        page = max(int(page_raw), 1)
    except ValueError:
        page = 1

    stats = get_dashboard_stats(app.config["DATABASE_URL"])
    pagination = get_registrants_paginated(
        page=page,
        per_page=50,
        sort_by=sort_by,
        db_path=app.config["DATABASE_URL"],
    )
    daily_registrations = get_registrations_per_day(30, app.config["DATABASE_URL"])
    role_distribution = get_distribution_by_field("role", app.config["DATABASE_URL"])
    industry_distribution = get_distribution_by_field("industry", app.config["DATABASE_URL"])

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        pagination=pagination,
        registrants=pagination["rows"],
        daily_registrations=daily_registrations,
        role_distribution=role_distribution,
        industry_distribution=industry_distribution,
    )


@app.get("/admin/export/csv")
@admin_required
def admin_export_csv():
    rows = get_all_registrants_for_csv(app.config["DATABASE_URL"])

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "ID",
            "First Name",
            "Last Name",
            "Email",
            "Role",
            "Industry",
            "IP Address",
            "User Agent",
            "Referrer",
            "Registered At",
            "Waitlist Position",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row["id"],
                row["first_name"],
                row["last_name"],
                row["email"],
                row["role"] or "",
                row["industry"] or "",
                row["ip_address"] or "",
                row["user_agent"] or "",
                row["referrer"] or "",
                row["registered_at"],
                row["position"],
            ]
        )

    csv_data = buffer.getvalue()
    buffer.close()

    filename = f"waitlist_registrants_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    response = Response(csv_data, mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@app.get("/admin/logout")
def admin_logout():
    token = session.get("admin_session_token")
    if token:
        delete_admin_session(token, app.config["DATABASE_URL"])
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("admin_login"))


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=5000, debug=debug_mode, use_reloader=False)
