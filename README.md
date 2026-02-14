# 3GPP Specifications Scraper

Automated scraper for 3GPP security specifications (33 series). Downloads, converts, and parses specifications into XML format.

## Features

- Downloads latest versions of 3GPP TS 33.xxx specifications
- Converts .doc files to .docx format using LibreOffice
- Generates PDF files from DOCX
- Parses specifications and extracts requirements/test cases into XML
- Automated updates via GitHub Actions (every 2 weeks)

## Repository Structure

```
TS 33.117 - General Requirements/
└── v.19.2.0/
    ├── 33117-v1920.docx
    ├── 33117-v1920.pdf
    └── 33117-v1920.xml
```

## Local Usage

### Prerequisites

```bash
# Install LibreOffice (required for PDF generation)
brew install --cask libreoffice  # macOS
# or
sudo apt-get install libreoffice  # Linux

# Install Python dependencies
pip install -r requirements.txt
```

### Run Scraper

```bash
python scraper.py
```

The scraper will:
- Download latest specifications from 3GPP
- Convert to DOCX, PDF, and XML formats
- Save files in version folders
