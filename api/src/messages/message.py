from messages.models import MessageTemplate, Message
from service.config import get_public_url


def send_message(template: MessageTemplate, member, db_session=None, render_template=None, **kwargs):
    
    if render_template is None:
        from flask import render_template
    
    subject = render_template(
        f"{template.value}.subject.html",
        public_url=get_public_url,
        member=member,
        **kwargs,
    )
    
    body = render_template(
        f"{template.value}.body.html",
        public_url=get_public_url,
        member=member,
        **kwargs,
    )

    if not db_session:
        from service.db import db_session
    
    db_session.add(Message(
        subject=subject,
        body=body,
        member_id=member.member_id,
        recipient=member.email,
        status=Message.QUEUED,
        template=template.value,
    ))
    
