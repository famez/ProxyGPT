from mitmproxy import http, ctx, websocket
import re
import os
from datetime import datetime, timezone
from pymongo import MongoClient

import grpc
import monitor_pb2
import monitor_pb2_grpc

from proxy import Proxy
from sites.chatgpt import ChatGPT
from sites.github_copilot import Github_Copilot
from sites.microsoft_copilot import Microsoft_Copilot
from sites.deepseek import DeepSeek
from sites.blackbox import BlackBox


from mitm_term import launch_ws_term

launch_ws_term()


db_client = MongoClient(os.getenv("MONGO_URI"))
events_collection = db_client["ProxyDLP"]["events"]
domains_collection = db_client["ProxyDLP"]["domains"]
sites_collection = db_client["ProxyDLP"]["sites"]
domain_settings_collection = db_client["ProxyDLP"]["domain-settings"]


#Anonymous access is allowed if no account check is enabled to authorize several account domains
def allow_anonymous_access(site):
    #Check domain check skip
    domain_settings = domain_settings_collection.find_one()
    if not domain_settings or not "check_domain" in domain_settings or not domain_settings['check_domain']:
        return True
    
    return False

#Anonymous conversations
def anonymous_conversation_callback(site, content, source_ip):
    event = {"timestamp": datetime.now(timezone.utc), "rational": "Conversation", "content": content, "site": site.get_name(), "source_ip": source_ip}
    result = events_collection.insert_one(event)
    mon_message = monitor_pb2.EventID(id=str(result.inserted_id))
    ctx.log.info("Sent event to monitor...")
    response = stub.EventAdded(mon_message)
    ctx.log.info(f"Response: {response}")

def account_login_callback(site, email, source_ip):

    #Check domain check skip
    domain_settings = domain_settings_collection.find_one()
    if not domain_settings or not "check_domain" in domain_settings or not domain_settings['check_domain']:
        return True        

    for domain in domains_collection.find():

        email_regex = r'^[a-zA-Z0-9._%+-]+@' + domain['content'] + '$'

        if re.match(email_regex, email):
            ctx.log.info(f"Corporative user {email} logged in")
            #Register event into the database.
            event = {"timestamp": datetime.now(timezone.utc), "user": email, "rational": "Logged in", "site": site.get_name(), "source_ip": source_ip}
            events_collection.insert_one(event)
            return True

    ctx.log.info(f"Email address does not belong to an organization")
    return False


def account_check_callback(site, email, source_ip):

    #Check domain check skip
    domain_settings = domain_settings_collection.find_one()
    if not domain_settings or not "check_domain" in domain_settings or not domain_settings['check_domain']:
        return True 
    
    for domain in domains_collection.find():
        email_regex = r'^[a-zA-Z0-9._%+\-*]+@' + domain['content'] + r'$'
        if re.match(email_regex, email):
            return True
    return False


def conversation_callback(site, email, content, source_ip):

    event = {"timestamp": datetime.now(timezone.utc), "user": email, "rational": "Conversation", "content": content, "site": site.get_name(), "source_ip": source_ip}
    result = events_collection.insert_one(event)
    mon_message = monitor_pb2.EventID(id=str(result.inserted_id))
    ctx.log.info("Sent event to monitor...")
    response = stub.EventAdded(mon_message)
    ctx.log.info(f"Response: {response}")

def attached_file_callback(site, email, filename, filepath, content_type, source_ip):

    event = {"timestamp": datetime.now(timezone.utc), "user": email, "rational": "Attached file", "filename" : filename, "filepath" : filepath, 
                        "content_type": content_type, "site": site.get_name(), "source_ip": source_ip}
                
    result = events_collection.insert_one(event)
    
    mon_message = monitor_pb2.EventID(id=str(result.inserted_id))

    ctx.log.info("Sent event to monitor...")

    response = stub.EventAdded(mon_message)

    ctx.log.info(f"Response: {response}")


proxy = Proxy(account_login_callback, account_check_callback, conversation_callback, attached_file_callback,
              allow_anonymous_access, anonymous_conversation_callback)


proxy.register_site(ChatGPT, ["openai.com", "chatgpt.com", "oaiusercontent.com"])
proxy.register_site(Microsoft_Copilot, ["substrate.office.com/m365Copilot/Chathub", "sharepoint.com/personal", "graph.microsoft.com/v1.0/me/drive/special/copilotuploads:"])
proxy.register_site(Github_Copilot, ["githubcopilot.com", "api.github.com/user"])
proxy.register_site(DeepSeek, ["deepseek.com"])
proxy.register_site(BlackBox, ["blackbox.ai"])

#Add sites to the database for being checked later on the web interface.
for site in proxy.get_sites():
    #ctx.log.info(f"Registering site {site.get_name()} with urls {site.get_urls()}")
    
    try:
        result = sites_collection.insert_one({
            "name": site.get_name(),
            "urls": site.get_urls()
        })
    except Exception as e:
        #ctx.log.error(f"Failed to register site {site.get_name()}: {e}")
        pass


channel = grpc.insecure_channel('monitor:50051')
stub = monitor_pb2_grpc.MonitorStub(channel)


def request(flow: http.HTTPFlow) -> None:
    proxy.route_request(flow)

def response(flow: http.HTTPFlow) -> None:
    proxy.route_response(flow)


class WSHandler:
    def websocket_message(self, flow):
        # This is called when a WebSocket message is received or sent.
        message = flow.websocket.messages[-1]
        if message.from_client:
            #ctx.log.info(f"Client -> Server: {message.content}")
            #ctx.log.info(f"Request URL: {flow.request.pretty_url}")
            proxy.route_ws_from_client_to_server(flow, message)
        

addons = [WSHandler()]

