#!/usr/bin/env -S sh -c 'exec "$(dirname "$0")/.venv/bin/python3" "$0" "$@"'
import email, base64, re, logging, os, urllib.request, subprocess
from email.parser import BytesParser
from pypdf import PdfWriter
from boto3 import client

logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',level=logging.INFO,datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

if 'FAXFROMS3_ALLOWED_EMAILS' in os.environ:
    allowed_emails = os.environ['FAXFROMS3_ALLOWED_EMAILS'].split(',')
else:
    logger.error("Could not read the allowed emails from the environment variable FAXFROMS3_ALLOWED_EMAILS.")
    quit(1)

if 'FAXFROMS3_BUCKET' in os.environ:
    bucket = os.environ['FAXFROMS3_BUCKET']
else:
    logger.error("Could not read the bucket the environment variable FAXFROMS3_BUCKET.")
    quit(1)

if 'FAXFROMD3_OUTGOING_DIR' in os.environ:
    outgoing_dir = os.path.join('/',os.environ['FAXFROMD3_OUTGOING_DIR'],'')
else:
    outgoing_dir = '/var/spool/asterisk/outgoing/'

try:
    test_filename = os.path.join(outgoing_dir, 'test')
    with open(f"{test_filename}", 'w') as call_file:
        call_file.write("")
    os.remove(f"{test_filename}")
except:
    logger.error(f"Could not create a test file in callfile directory {outgoing_dir}.")
    quit(1)

logger.info('Looking for emails in the S3 bucket.')
s3connection = client('s3')  # again assumes boto.cfg setup, assume AWS S3
s3_objectdict = s3connection.list_objects(Bucket='fax.dn358.com',Prefix='in/')
if 'Contents' in s3_objectdict:
    for object_info in s3_objectdict['Contents']:
        logger.info(f"Reading email {object_info['Key']} from S3.")
        
        unique_id = object_info['Key'].removeprefix('in/')
        this_object = s3connection.get_object(Bucket='fax.dn358.com', Key=object_info['Key'])
        try:
            this_email = this_object['Body'].read()
            parsed_email = BytesParser().parsebytes(this_email)
            if ('from' not in parsed_email or 'to' not in parsed_email):
                logger.warning(f"Couldn't find from and to fields, looks like this isn't an email.")
                parsed_email = False
        except:
            logger.warning(f"Exception when trying to parse this, so treating it like it's not an email.")
            parsed_email = False
        if parsed_email:
            logger.info(f"HEADERS to {parsed_email['to']}  from {parsed_email['from']}")
            from_addresspart = email.utils.parseaddr(parsed_email['from'])[1]
            to_addresspart = email.utils.parseaddr(parsed_email['to'])[1]
            if from_addresspart not in allowed_emails:
                logger.error(f"The email address {from_addresspart} is not allowed to use this service.")
                quit(1)
            mailbox_match = re.match(r"^([^@]+)@.*", to_addresspart)
            mailbox_part = mailbox_match.group(1)
            phone_match = re.match(r"^[+]?[1]?\d{10}$", mailbox_part)
            if not phone_match:
                logger.error(f"The phone number provided ({mailbox_part}) is not a valid phone number.")
                quit(1)
            phone_number = phone_match.group(0)
            attachment_counter = 0
            if parsed_email.is_multipart():
                for part in parsed_email.walk():
                    if part.get_content_type() == "application/pdf":
                        attachment_counter += 1
                        pdf_filename = os.path.join('/tmp/',f"{unique_id}-{attachment_counter}.pdf")
                        decoded_bytes = base64.b64decode(part.get_payload())
                        logger.info(f"Getting {part.get_filename()} from the email and writing it as {pdf_filename}.")
                        with open(pdf_filename, 'wb') as pdf_file:
                            pdf_file.write(decoded_bytes)
            if attachment_counter > 0:
                pdf_filename = os.path.join('/tmp/',f"{unique_id}.pdf")
                tif_filename = os.path.join('/tmp/',f"{unique_id}.tif")
                logger.info(f"Combining/renaming {attachment_counter} PDF files into {pdf_filename}.")
                if attachment_counter > 1:
                    combined_pdf = PdfWriter()
                    all_pdfs = [os.path.join('/tmp/',f"{unique_id}-{counter}.pdf") for counter in range(1,attachment_counter+1)]
                    for pdf in all_pdfs:
                        combined_pdf.append(pdf)
                    combined_pdf.write(pdf_filename)
                    combined_pdf.close()
                    for pdf in all_pdfs:
                        os.remove(pdf)
                else:
                    os.rename(os.path.join('/tmp/',f"{unique_id}-{attachment_counter}.pdf"),pdf_filename)
                conversion_args = args = ["gs","-q","-dNOPAUSE","-sDEVICE=tiffg4","-sPAPERSIZE=letter",f"-sOutputFile={tif_filename}",f"{pdf_filename}","-c","quit"]
                subprocess.call(args)
                os.remove(pdf_filename)
                call_filename = os.path.join(outgoing_dir, f"{unique_id}")
                call_text =  f"Channel: Local/{phone_number}@from-faxfile" + ("\r\n")
                call_text += f"Context: to-sendfax" + ("\r\n")
                call_text += f"Extension: {phone_number}" + ("\r\n")
                call_text += f"Priority: 1" + ("\r\n")
                call_text += f"MaxRetries: 2" + ("\r\n")
                call_text += f"Setvar: FAXFILE={unique_id}" + ("\r\n")
                logger.info(f"Creating callfile as {call_filename}.")
                with open(call_filename, 'w') as call_file:
                    call_file.writelines(call_text)
        logger.info(f"Done processing this email, so deleting {object_info['Key']}")
        s3connection.delete_object(Bucket='fax.dn358.com', Key=object_info['Key'])
else:
    logger.info("No files to process.") 
