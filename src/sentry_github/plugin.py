"""
sentry_github.plugin
~~~~~~~~~~~~~~~~~~~~

:copyright: (c) 2012 by the Sentry Team, see AUTHORS for more details.
:license: BSD, see LICENSE for more details.
"""
import requests
from urllib import urlencode
from rest_framework.response import Response
from django import forms
from django.contrib import messages
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _
from sentry.plugins.base import JSONResponse
from sentry.plugins.bases.issue2 import IssuePlugin2
from sentry.http import safe_urlopen, safe_urlread
from sentry.utils import json
from sentry.utils.http import absolute_uri

import sentry_github

class GitHubPlugin(IssuePlugin2):
    author = 'Sentry Team'
    author_url = 'https://github.com/getsentry/sentry'
    version = sentry_github.VERSION
    description = "Integrate GitHub issues by linking a repository to a project."
    resource_links = [
        ('Bug Tracker', 'https://github.com/getsentry/sentry-github/issues'),
        ('Source', 'https://github.com/getsentry/sentry-github'),
    ]

    slug = 'github'
    title = 'GitHub'
    conf_title = title
    conf_key = 'github'
    auth_provider = 'github'
    create_issue_template = 'sentry_github/create_github_issue.html'
    can_unlink_issues = True
    can_link_existing_issues = True

    def is_configured(self, request, project, **kwargs):
        return bool(self.get_option('repo', project))

    def get_new_issue_title(self, **kwargs):
        return 'Link GitHub Issue'

    def get_unlink_issue_title(self, **kwargs):
        return 'Unlink GitHub Issue'

    def get_new_issue_read_only_fields(self, **kwargs):
        group = kwargs.get('group')
        if group:
            return [{'label': 'Github Repository', 'value': self.get_option('repo', group.project)}]
        return []

    def handle_api_error(self, request, error):
        msg = _('Error communicating with GitHub: %s') % error
        messages.add_message(request, messages.ERROR, msg)

    def get_allowed_assignees(self, request, group):
        try:
            url = self.build_api_url(group, 'assignees')
            req = self.make_api_request(request.user, url)
            body = safe_urlread(req)
        except requests.RequestException as e:
            msg = unicode(e)
            self.handle_api_error(request, msg)
            return tuple()

        try:
            json_resp = json.loads(body)
        except ValueError as e:
            msg = unicode(e)
            self.handle_api_error(request, msg)
            return tuple()

        if req.status_code > 399:
            self.handle_api_error(request, json_resp.get('message', ''))
            return tuple()

        users = tuple((u['login'], u['login']) for u in json_resp)

        return (('', 'Unassigned'),) + users

    def get_initial_link_form_data(self, request, group, event, **kwargs):
        return {'comment': absolute_uri(group.get_absolute_url())}

    def get_new_issue_form(self, request, group, event, **kwargs):
        """
        Return a Form for the "Create new issue" page.
        """
        return self.new_issue_form(self.get_allowed_assignees(request, group),
                                   request.POST or None,
                                   initial=self.get_initial_form_data(request, group, event))

    def build_api_url(self, group, github_api, query_params=None):
        repo = self.get_option('repo', group.project)

        url = 'https://api.github.com/repos/%s/%s' % (repo, github_api)

        if query_params:
            url = '%s?%s' % (url, urlencode(query_params))

        return url

    def make_api_request(self, user, url, json_data=None):
        auth = self.get_auth_for_user(user=user)
        if auth is None:
            raise forms.ValidationError(_('You have not yet associated GitHub with your account.'))

        req_headers = {
            'Authorization': 'token %s' % auth.tokens['access_token'],
        }
        return safe_urlopen(url, json=json_data, headers=req_headers, allow_redirects=True)

    def create_issue(self, request, group, form_data, **kwargs):
        # TODO: support multiple identities via a selection input in the form?
        json_data = {
            "title": form_data['title'],
            "body": form_data['description'],
            "assignee": form_data.get('assignee'),
        }

        try:
            url = self.build_api_url(group, 'issues')
            req = self.make_api_request(request.user, url, json_data=json_data)
            body = safe_urlread(req)
        except requests.RequestException as e:
            msg = unicode(e)
            raise forms.ValidationError(_('Error communicating with GitHub: %s') % (msg,))

        try:
            json_resp = json.loads(body)
        except ValueError as e:
            msg = unicode(e)
            raise forms.ValidationError(_('Error communicating with GitHub: %s') % (msg,))

        if req.status_code > 399:
            raise forms.ValidationError(json_resp['message'])

        return json_resp['number']

    def link_issue(self, request, group, form_data, **kwargs):
        comment = form_data.get('comment')
        if not comment:
            return
        url = '%s/%s/comments' % (self.build_api_url(group, 'issues'), form_data['issue_id'])
        try:
            req = self.make_api_request(request.user, url, json_data={'body': comment})
            body = safe_urlread(req)
        except requests.RequestException as e:
            msg = unicode(e)
            raise forms.ValidationError(_('Error communicating with GitHub: %s') % (msg,))

        try:
            json_resp = json.loads(body)
        except ValueError as e:
            msg = unicode(e)
            raise forms.ValidationError(_('Error communicating with GitHub: %s') % (msg,))

        if req.status_code > 399:
            raise forms.ValidationError(json_resp['message'])

    def get_issue_label(self, group, issue_id, **kwargs):
        return 'GH-%s' % issue_id

    def get_issue_url(self, group, issue_id, **kwargs):
        # XXX: get_option may need tweaked in Sentry so that it can be pre-fetched in bulk
        repo = self.get_option('repo', group.project)

        return 'https://github.com/%s/issues/%s' % (repo, issue_id)

    def get_issue_title_by_id(self, request, group, issue_id):
        url = '%s/%s' % (self.build_api_url(group, 'issues'), issue_id)
        req = self.make_api_request(request.user, url)

        body = safe_urlread(req)
        json_resp = json.loads(body)
        return json_resp['title']

    def view_autocomplete(self, request, group, **kwargs):
        field = request.GET.get('autocomplete_field')
        query = request.GET.get('autocomplete_query')
        if field != 'issue_id' or not query:
            return Response({'issues': []})

        repo = self.get_option('repo', group.project)
        query = 'repo:%s %s' % (repo, query)
        url = 'https://api.github.com/search/issues?%s' % (urlencode({'q': query}),)

        try:
            req = self.make_api_request(request.user, url)
            body = safe_urlread(req)
        except requests.RequestException as e:
            msg = unicode(e)
            self.handle_api_error(request, msg)
            return JSONResponse({}, status_code=502)

        try:
            json_resp = json.loads(body)
        except ValueError as e:
            msg = unicode(e)
            self.handle_api_error(request, msg)
            return JSONResponse({}, status_code=502)

        issues = [{
            'text': '(#%s) %s' % (i['number'], i['title']),
            'id': i['number']
        } for i in json_resp.get('items', [])]

        return Response({field: issues})

    def get_configure_plugin_fields(self, request, project, **kwargs):
        return [{
            'name': 'repo',
            'label': 'Repository Name',
            'default': self.get_option('repo', project),
            'type': 'text',
            'placeholder': 'e.g. getsentry/sentry',
            'help_text': 'Enter your repository name, including the owner.'
        }]
