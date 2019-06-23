from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from contextlib import closing
from datetime import datetime
from time import sleep

import requests
from rocky.process import log_exception, stoppable
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from messages.models import Message
from service.config import get_mysql_config, config
from service.db import create_mysql_engine
from service.logging import logger


def send_messages(db_session, key, domain, sender, to_override, limit):
    query = db_session.query(Message)
    query = query.filter(Message.status == Message.QUEUED)
    query = query.limit(limit)
    
    for message in query:
        to = message.recipient
        msg = f"sending {message.id} to {to}"
        
        if to_override:
            msg += f" (overriding to {to_override})"
            to = to_override

        msg += f": {message.subject}"

        logger.info(msg)

        response = requests.post(f"https://api.mailgun.net/v3/{domain}/messages", auth=('api', key),
                                 data={
                                     'from': sender,
                                     'to': to,
                                     'subject': message.subject,
                                     'html': message.body,
                                 })
        
        if response.ok:
            message.status = 'sent'
            message.sent_at = datetime.utcnow()
            
            db_session.add(message)
            db_session.commit()
            
        else:
            message.status = 'failed'
            
            db_session.add(message)
            db_session.commit()
            
            logger.error(f"failed to send {message.id} to {to}: {response.content}")


if __name__ == '__main__':

    with log_exception(status=1), stoppable():
        parser = ArgumentParser(description="Dispatch emails in db send queue.",
                                formatter_class=ArgumentDefaultsHelpFormatter)
        
        parser.add_argument('--sleep', default=4, help='Sleep time (in seconds) between checking for messages to send.')
        parser.add_argument('--limit', default=10, help='Max messages to send every time checking for messages.')
        
        args = parser.parse_args()
        
        engine = create_mysql_engine(**get_mysql_config())
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        logger.info(f'checking for emails to send every {args.sleep} seconds, limit is {args.limit}')
        
        key = config.get('MAILGUN_KEY', log_value=False)
        domain = config.get('MAILGUN_DOMAIN')
        sender = config.get('MAILGUN_FROM')
        to_override = config.get('MAILGUN_TO_OVERRIDE')
        
        while True:
            sleep(args.sleep)
            with closing(session_factory()) as db_session:
                try:
                    send_messages(db_session, key, domain, sender, to_override, args.limit)
                except DatabaseError as e:
                    logger.warning(f"failed to access messages_recipient table, ignoring: {e}")
