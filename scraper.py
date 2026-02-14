import os
import zipfile
import requests
import subprocess
import glob
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from docx import Document
import shutil
import time

BASE = "https://www.3gpp.org/ftp/Specs/archive/33_series"
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

TS_SPECS = {
    "33.116": "MME",
    "33.117": "General Requirements",
    "33.216": "eNB",
    "33.226": "IMS",
    "33.250": "PGW",
    "33.326": "NSSAAF",
    "33.511": "gNodeB",
    "33.512": "AMF",
    "33.513": "UPF",
    "33.514": "UDM",
    "33.515": "SMF",
    "33.516": "AUSF",
    "33.517": "SEPP",
    "33.518": "NRF",
    "33.519": "NEF",
    "33.520": "N3IWF",
    "33.521": "NWDAF",
    "33.522": "SCP",
    "33.523": "Split gNB",
    "33.526": "Management Function",
    "33.527": "Virtualized network products",
    "33.528": "PCF",
    "33.530": "UDR",
    "33.537": "AKMA Anchor Function (AAnF)",
}


def parse_version_from_filename(filename):
    """
    Parse version from filename like '33117-j20.zip' or '33117-j20.xml'
    Returns tuple (major, minor, patch) or None if pattern not found

    Formula:
    - Letter position in alphabet + 9 = major version
    - Number after letter / 10 = minor version
    - Patch is always 0

    Example: j20 -> j=10, 10+9=19, 20/10=2 -> v.19.2.0
    """
    match = re.search(r"-([a-z])(\d+)", filename, re.IGNORECASE)
    if not match:
        return None

    letter = match.group(1).lower()
    number = int(match.group(2))

    # Calculate major version: letter position + 9
    major = ord(letter) - ord("a") + 1 + 9

    # Calculate minor version: number / 10
    minor = number // 10
    patch = 0

    return (major, minor, patch)


def format_version(major, minor, patch):
    """Format version tuple as string like 'v.19.2.0'"""
    return f"v.{major}.{minor}.{patch}"


def format_version_for_filename(major, minor, patch):
    """Format version for filename like 'v1920' from (19, 2, 0)"""
    return f"v{major}{minor}{patch}"


def get_latest_zip(ts):
    url = f"{BASE}/{ts}/"

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()

            soup = BeautifulSoup(r.text, "html.parser")
            zips = [
                a["href"]
                for a in soup.find_all("a", href=True)
                if a["href"].endswith(".zip")
            ]

            if not zips:
                raise RuntimeError(f"No ZIPs found for TS {ts}")

            return urljoin(url, sorted(zips)[-1])
        except (requests.RequestException, RuntimeError) as e:
            if attempt < MAX_RETRIES - 1:
                print(
                    f"  ⚠ Attempt {attempt + 1} failed: {e}. Retrying in {RETRY_DELAY}s..."
                )
                time.sleep(RETRY_DELAY)
            else:
                raise


def clean_text(text):
    """Removes non-ascii characters and extra whitespace."""
    if not text:
        return ""
    # Remove control characters but keep basic punctuation
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text.strip()


def parse_docx_to_xml(docx_path, xml_output_path, spec_number):
    """Parse a 3GPP specification DOCX file and convert it to XML format"""
    if Document is None:
        print("  ⚠ python-docx not installed, skipping XML conversion")
        return

    try:
        document = Document(docx_path)
    except Exception as e:
        print(f"  ✗ Failed to read {os.path.basename(docx_path)}: {e}")
        return

    # Create Root XML Element
    root = ET.Element("Specification")
    root.set("name", f"3GPP TS {spec_number}")

    # Stack to maintain section hierarchy (Heading 1 -> Heading 2 -> etc.)
    section_stack = [(0, root)]

    current_element = root
    current_req = None
    current_test = None
    capture_mode = None  # None, 'ReqDesc', 'TestPurpose', 'PreCond', 'Steps', 'Results', 'Evidence'

    # Regex patterns based on the 3GPP document style
    patterns = {
        "req_name": re.compile(r"^Requirement Name:\s*(.*)", re.IGNORECASE),
        "req_ref": re.compile(r"^Requirement Reference:\s*(.*)", re.IGNORECASE),
        "req_desc": re.compile(r"^Requirement Description:\s*(.*)", re.IGNORECASE),
        "threat_ref": re.compile(r"^Threat References:\s*(.*)", re.IGNORECASE),
        "test_name": re.compile(r"^Test Name:\s*(.*)", re.IGNORECASE),
        "purpose": re.compile(r"^Purpose:\s*(.*)", re.IGNORECASE),
        "pre_cond": re.compile(r"^Pre-Conditions:\s*(.*)", re.IGNORECASE),
        "steps": re.compile(r"^Execution Steps", re.IGNORECASE),
        "expected": re.compile(r"^Expected Results:\s*(.*)", re.IGNORECASE),
        "evidence": re.compile(r"^Expected format of evidence:\s*(.*)", re.IGNORECASE),
    }

    for para in document.paragraphs:
        text = clean_text(para.text)
        if not text:
            continue

        style = para.style.name

        # --- Handle Headings (Structure) ---
        if style.startswith("Heading"):
            try:
                level = int(style.split()[-1])
            except ValueError:
                level = 1  # Fallback

            # Close pending items
            current_req = None
            current_test = None
            capture_mode = None

            # Pop stack until we find the parent level
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()

            parent = section_stack[-1][1]
            new_section = ET.SubElement(parent, "Section")
            new_section.set("title", text)
            new_section.set("level", str(level))

            # Push new section to stack
            section_stack.append((level, new_section))
            current_element = new_section
            continue

        # --- Handle Requirements ---
        match = patterns["req_name"].match(text)
        if match:
            capture_mode = None
            current_test = None
            current_req = ET.SubElement(current_element, "Requirement")
            name_el = ET.SubElement(current_req, "Name")
            name_el.text = match.group(1).strip()
            continue

        if current_req is not None:
            match = patterns["req_ref"].match(text)
            if match:
                ref = ET.SubElement(current_req, "Reference")
                ref.text = match.group(1).strip()
                continue

            match = patterns["req_desc"].match(text)
            if match:
                desc = ET.SubElement(current_req, "Description")
                desc.text = match.group(1).strip()
                capture_mode = "ReqDesc"
                continue

            match = patterns["threat_ref"].match(text)
            if match:
                threat = ET.SubElement(current_req, "ThreatReference")
                threat.text = match.group(1).strip()
                capture_mode = None
                continue

        # --- Handle Test Cases ---
        match = patterns["test_name"].match(text)
        if match:
            capture_mode = None
            current_test = ET.SubElement(current_element, "TestCase")
            tc_name = ET.SubElement(current_test, "Name")
            tc_name.text = match.group(1).strip()
            continue

        if current_test is not None:
            match = patterns["purpose"].match(text)
            if match:
                purpose = ET.SubElement(current_test, "Purpose")
                purpose.text = match.group(1).strip()
                capture_mode = "TestPurpose"
                continue

            match = patterns["pre_cond"].match(text)
            if match:
                pre = ET.SubElement(current_test, "PreConditions")
                pre.text = match.group(1).strip()
                capture_mode = "PreCond"
                continue

            match = patterns["steps"].match(text)
            if match:
                steps = ET.SubElement(current_test, "ExecutionSteps")
                capture_mode = "Steps"
                continue

            match = patterns["expected"].match(text)
            if match:
                exp = ET.SubElement(current_test, "ExpectedResults")
                exp.text = match.group(1).strip()
                capture_mode = "Results"
                continue

            match = patterns["evidence"].match(text)
            if match:
                ev = ET.SubElement(current_test, "EvidenceFormat")
                ev.text = match.group(1).strip()
                capture_mode = "Evidence"
                continue

        # --- Handle Multi-line Content (Capture Mode) ---
        if capture_mode and (current_req or current_test):
            target_node = None
            if capture_mode == "ReqDesc" and current_req:
                target_node = current_req.find("Description")
            elif current_test:
                if capture_mode == "TestPurpose":
                    target_node = current_test.find("Purpose")
                elif capture_mode == "PreCond":
                    target_node = current_test.find("PreConditions")
                elif capture_mode == "Steps":
                    target_node = current_test.find("ExecutionSteps")
                elif capture_mode == "Results":
                    target_node = current_test.find("ExpectedResults")
                elif capture_mode == "Evidence":
                    target_node = current_test.find("EvidenceFormat")

            if target_node is not None:
                if target_node.text:
                    target_node.text += "\n" + text
                else:
                    target_node.text = text

    # Prettify XML
    xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")

    try:
        with open(xml_output_path, "w", encoding="utf-8") as f:
            f.write(xml_str)
        print(f"  → Generated {os.path.basename(xml_output_path)}")
    except Exception as e:
        print(f"  ✗ Failed to write XML: {e}")


def convert_docx_to_xml(base_folder, spec_number):
    """Convert all .docx files in folder to XML"""
    if Document is None:
        return

    docx_files = glob.glob(os.path.join(base_folder, "**", "*.docx"), recursive=True)

    for docx_file in docx_files:
        # Generate XML filename (same name but .xml extension)
        xml_file = docx_file.rsplit(".", 1)[0] + ".xml"
        parse_docx_to_xml(docx_file, xml_file, spec_number)


def find_libreoffice():
    """Find LibreOffice executable path"""
    soffice_paths = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",  # macOS
        "/usr/bin/soffice",  # Linux (GitHub Actions, apt install)
        "/usr/local/bin/soffice",  # Linux (alternative)
        "soffice",  # fallback to PATH
    ]

    for path in soffice_paths:
        if path == "soffice":
            try:
                result = subprocess.run(
                    ["which", "soffice"], capture_output=True, timeout=5
                )
                if result.returncode == 0:
                    return "soffice"
            except:
                pass
        elif os.path.exists(path):
            return path

    return None


def convert_doc_to_docx(base_folder):
    """Convert all .doc files to .docx using LibreOffice headless mode"""
    soffice = find_libreoffice()

    if not soffice:
        print(
            "  ⚠ LibreOffice not found. Install with: brew install --cask libreoffice (macOS) or apt-get install libreoffice (Linux)"
        )
        return

    doc_files = glob.glob(os.path.join(base_folder, "**", "*.doc"), recursive=True)

    for doc_file in doc_files:
        # Skip if it's already a .docx file
        if doc_file.endswith(".docx"):
            continue

        try:
            # Get the directory containing the .doc file
            doc_dir = os.path.dirname(doc_file)

            # Convert using LibreOffice headless mode
            subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    doc_dir,
                    doc_file,
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )

            # Remove original .doc file after successful conversion
            os.remove(doc_file)
            print(f"  → Converted {os.path.basename(doc_file)} to .docx")
        except subprocess.CalledProcessError as e:
            print(f"  ✗ Failed to convert {os.path.basename(doc_file)}: {e}")
        except Exception as e:
            print(f"  ✗ Error processing {os.path.basename(doc_file)}: {e}")


def convert_docx_to_pdf(docx_path, pdf_path):
    """Convert a DOCX file to PDF using LibreOffice"""
    soffice = find_libreoffice()

    if not soffice:
        print("  ⚠ LibreOffice not found, skipping PDF generation")
        return False

    try:
        doc_dir = os.path.dirname(pdf_path)
        temp_dir = os.path.dirname(docx_path)

        # Convert to PDF in temporary location
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                temp_dir,
                docx_path,
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )

        # Move PDF to final location
        temp_pdf = docx_path.rsplit(".", 1)[0] + ".pdf"
        if os.path.exists(temp_pdf):
            shutil.move(temp_pdf, pdf_path)
            print(f"  → Generated {os.path.basename(pdf_path)}")
            return True

        return False
    except subprocess.CalledProcessError as e:
        print(f"  ✗ Failed to generate PDF: {e}")
        return False
    except Exception as e:
        print(f"  ✗ Error generating PDF: {e}")
        return False


def download_extract_cleanup(ts, name):
    base_folder = f"TS {ts} - {name}"
    os.makedirs(base_folder, exist_ok=True)

    zip_url = get_latest_zip(ts)
    zip_name = os.path.basename(zip_url)

    # Parse version from ZIP filename
    version_tuple = parse_version_from_filename(zip_name)
    if not version_tuple:
        print(f"  ⚠ Could not parse version from {zip_name}")
        return

    major, minor, patch = version_tuple
    version_str = format_version(major, minor, patch)
    version_filename = format_version_for_filename(major, minor, patch)

    # Create version folder
    version_folder = os.path.join(base_folder, version_str)
    os.makedirs(version_folder, exist_ok=True)

    # Temporary extraction folder
    temp_folder = os.path.join(base_folder, "_temp")
    os.makedirs(temp_folder, exist_ok=True)

    zip_path = os.path.join(temp_folder, zip_name)

    print(f"↓ TS {ts} ({version_str})")

    # Download ZIP with retry logic
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(zip_url, timeout=60)
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                f.write(r.content)
            break
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  ⚠ Download attempt {attempt + 1} failed. Retrying...")
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(
                    f"Failed to download after {MAX_RETRIES} attempts: {e}"
                )

    # Extract ZIP to temp folder
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(temp_folder)

    # Delete ZIP after successful extraction
    os.remove(zip_path)

    # Convert .doc files to .docx
    convert_doc_to_docx(temp_folder)

    # Convert .docx files to XML
    convert_docx_to_xml(temp_folder, ts)

    # Find and process the DOCX and XML files
    spec_number = ts.replace(".", "")
    docx_files = glob.glob(os.path.join(temp_folder, "**", "*.docx"), recursive=True)
    xml_files = glob.glob(os.path.join(temp_folder, "**", "*.xml"), recursive=True)

    # Process DOCX files
    for docx_file in docx_files:
        new_docx_name = f"{spec_number}-{version_filename}.docx"
        new_docx_path = os.path.join(version_folder, new_docx_name)
        shutil.copy2(docx_file, new_docx_path)
        print(f"  → Saved {new_docx_name}")

        # Generate PDF from DOCX
        new_pdf_name = f"{spec_number}-{version_filename}.pdf"
        new_pdf_path = os.path.join(version_folder, new_pdf_name)
        convert_docx_to_pdf(new_docx_path, new_pdf_path)

    # Process XML files
    for xml_file in xml_files:
        new_xml_name = f"{spec_number}-{version_filename}.xml"
        new_xml_path = os.path.join(version_folder, new_xml_name)
        shutil.copy2(xml_file, new_xml_path)
        print(f"  → Saved {new_xml_name}")

    # Clean up temp folder
    shutil.rmtree(temp_folder)

    print(f"✓ TS {ts} ({version_str}) complete")


def main():
    failed_specs = []
    successful_specs = []

    print(f"Starting download of {len(TS_SPECS)} specifications...\n")

    for ts, name in TS_SPECS.items():
        try:
            download_extract_cleanup(ts, name)
            successful_specs.append(ts)
        except Exception as e:
            print(f"✗ TS {ts} failed: {e}")
            failed_specs.append((ts, str(e)))

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  ✓ Successful: {len(successful_specs)}/{len(TS_SPECS)}")

    if failed_specs:
        print(f"  ✗ Failed: {len(failed_specs)}")
        for ts, error in failed_specs:
            print(f"    - TS {ts}: {error}")
        print(f"\n⚠ Warning: Some specifications failed to download")
    else:
        print(f"  All specifications downloaded successfully!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
