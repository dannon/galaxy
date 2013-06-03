<%inherit file="/base.mako"/>
<%namespace file="/message.mako" import="render_msg" />
<%namespace file="/webapps/tool_shed/common/common.mako" import="*" />
<%namespace file="/webapps/tool_shed/repository/common.mako" import="*" />
<%namespace file="/webapps/tool_shed/common/repository_actions_menu.mako" import="render_tool_shed_repository_actions" />

<%
    from tool_shed.util.encoding_util import tool_shed_encode
%>

<%!
   def inherit(context):
       if context.get('use_panels'):
           return '/webapps/tool_shed/base_panels.mako'
       else:
           return '/base.mako'
%>

<%inherit file="${inherit(context)}"/>

<%def name="render_workflow( workflow_name, repository_metadata_id )">
    <% center_url = h.url_for( controller='repository', action='generate_workflow_image', workflow_name=tool_shed_encode( workflow_name ), repository_metadata_id=repository_metadata_id ) %>
    <iframe name="galaxy_main" id="galaxy_main" frameborder="0" style="position: absolute; width: 100%; height: 100%;" src="${center_url}"> </iframe>
</%def>

${render_tool_shed_repository_actions( repository=repository )}

%if message:
    ${render_msg( message, status )}
%endif

<div class="toolFormTitle">${workflow_name | h}</div>
<div class="form-row">
    <b>Boxes are red when tools are not available in this repository</b>
    <div class="toolParamHelp" style="clear: both;">
        (this page displays SVG graphics)
    </div>
</div>
<br clear="left"/>

${render_workflow( workflow_name, repository_metadata_id )}
