import streamlit as st
from datetime import datetime
import lakebase

# App title
st.title("⚡ ThunderHawk Ticketing System")
st.markdown("---")

# Create tabs for different features
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📋 View All Tickets",
    "💬 View Messages",
    "➕ Create Ticket",
    "✉️ Add Message",
    "🔄 Update Status",
    "📊 Statistics"
])

# Tab 1: View All Tickets
with tab1:
    st.header("All Support Tickets")
    
    try:
        rows = lakebase.run_query("""
            SELECT 
                t.ticket_id,
                t.title,
                t.status,
                t.created_by,
                t.created_at,
                COUNT(tm.message_id) as message_count
            FROM ticketing.tickets t
            LEFT JOIN ticketing.ticket_messages tm ON t.ticket_id = tm.ticket_id
            GROUP BY t.ticket_id, t.title, t.status, t.created_by, t.created_at
            ORDER BY t.created_at DESC
        """)
        
        if rows:
            for ticket in rows:
                ticket_id = ticket['ticket_id']
                title = ticket['title']
                status = ticket['status']
                created_by = ticket['created_by']
                created_at = ticket['created_at']
                msg_count = ticket['message_count']
                
                # Status badge color
                status_color = {
                    'open': '🔴',
                    'in_progress': '🟡',
                    'resolved': '🟢'
                }.get(status.lower(), '⚪')
                
                with st.container():
                    col1, col2, col3, col4 = st.columns([1, 3, 2, 2])
                    with col1:
                        st.write(f"**#{ticket_id}**")
                    with col2:
                        st.write(f"**{title}**")
                    with col3:
                        st.write(f"{status_color} {status}")
                    with col4:
                        st.write(f"💬 {msg_count} messages")
                    
                    st.caption(f"Created by {created_by} on {created_at}")
                    st.markdown("---")
        else:
            st.info("No tickets found.")
        
    except Exception as e:
        st.error(f"Error loading tickets: {str(e)}")

# Tab 2: View Messages
with tab2:
    st.header("View Ticket Messages")
    
    try:
        # Get list of tickets for dropdown
        tickets = lakebase.run_query("""
            SELECT ticket_id, title, status
            FROM ticketing.tickets
            ORDER BY ticket_id DESC
        """)
        
        if tickets:
            ticket_options = {f"#{t['ticket_id']} - {t['title']} ({t['status']})": t['ticket_id'] for t in tickets}
            selected_ticket = st.selectbox(
                "Select a ticket to view messages:",
                options=list(ticket_options.keys())
            )
            
            if selected_ticket:
                ticket_id = ticket_options[selected_ticket]
                
                # Get ticket details
                ticket_info = lakebase.run_query("""
                    SELECT title, status, created_by, created_at
                    FROM ticketing.tickets
                    WHERE ticket_id = %s
                """, (ticket_id,))
                
                if ticket_info:
                    info = ticket_info[0]
                    st.subheader(f"Ticket #{ticket_id}: {info['title']}")
                    st.write(f"**Status:** {info['status']}")
                    st.write(f"**Created by:** {info['created_by']} on {info['created_at']}")
                    st.markdown("---")
                    
                    # Get messages
                    messages = lakebase.run_query("""
                        SELECT message_id, message_text, author, created_at
                        FROM ticketing.ticket_messages
                        WHERE ticket_id = %s
                        ORDER BY created_at ASC
                    """, (ticket_id,))
                    
                    if messages:
                        for msg in messages:
                            with st.chat_message("user" if msg['author'] != "support@company.com" else "assistant"):
                                st.write(f"**{msg['author']}**")
                                st.write(msg['message_text'])
                                st.caption(f"{msg['created_at']}")
                    else:
                        st.info("No messages yet for this ticket.")
        else:
            st.info("No tickets available.")
        
    except Exception as e:
        st.error(f"Error loading messages: {str(e)}")

# Tab 3: Create New Ticket
with tab3:
    st.header("Create New Support Ticket")
    
    with st.form("create_ticket_form"):
        title = st.text_input("Ticket Title *", placeholder="Brief description of the issue")
        status = st.selectbox("Status *", ["open", "in_progress", "resolved"])
        created_by = st.text_input("Your Email *", placeholder="user@company.com")
        initial_message = st.text_area("Initial Message *", placeholder="Describe the issue in detail...")
        
        submitted = st.form_submit_button("Create Ticket")
        
        if submitted:
            if not title or not created_by or not initial_message:
                st.error("Please fill in all required fields.")
            else:
                try:
                    # Insert ticket and get the new ticket_id
                    with lakebase.get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO ticketing.tickets (title, status, created_by, created_at)
                                VALUES (%s, %s, %s, %s)
                                RETURNING ticket_id
                            """, (title, status, created_by, datetime.now()))
                            
                            ticket_id = cur.fetchone()['ticket_id']
                            
                            # Insert initial message
                            cur.execute("""
                                INSERT INTO ticketing.ticket_messages (ticket_id, message_text, author, created_at)
                                VALUES (%s, %s, %s, %s)
                            """, (ticket_id, initial_message, created_by, datetime.now()))
                            
                            conn.commit()
                    
                    st.success(f"✅ Ticket #{ticket_id} created successfully!")
                    
                except Exception as e:
                    st.error(f"Error creating ticket: {str(e)}")

# Tab 4: Add Message to Ticket
with tab4:
    st.header("Add Message to Existing Ticket")
    
    try:
        # Get list of tickets
        tickets = lakebase.run_query("""
            SELECT ticket_id, title, status
            FROM ticketing.tickets
            ORDER BY ticket_id DESC
        """)
        
        if tickets:
            ticket_options = {f"#{t['ticket_id']} - {t['title']} ({t['status']})": t['ticket_id'] for t in tickets}
            
            with st.form("add_message_form"):
                selected_ticket = st.selectbox(
                    "Select Ticket:",
                    options=list(ticket_options.keys())
                )
                author = st.text_input("Your Email *", placeholder="user@company.com")
                message_text = st.text_area("Message *", placeholder="Enter your message...")
                
                submitted = st.form_submit_button("Add Message")
                
                if submitted:
                    if not author or not message_text:
                        st.error("Please fill in all required fields.")
                    else:
                        try:
                            ticket_id = ticket_options[selected_ticket]
                            
                            lakebase.run_write("""
                                INSERT INTO ticketing.ticket_messages (ticket_id, message_text, author, created_at)
                                VALUES (%s, %s, %s, %s)
                            """, (ticket_id, message_text, author, datetime.now()))
                            
                            st.success(f"✅ Message added to Ticket #{ticket_id}!")
                            
                        except Exception as e:
                            st.error(f"Error adding message: {str(e)}")
        else:
            st.info("No tickets available to add messages to.")
        
    except Exception as e:
        st.error(f"Error: {str(e)}")

# Tab 5: Update Ticket Status
with tab5:
    st.header("Update Ticket Status")
    
    try:
        # Get list of tickets
        tickets = lakebase.run_query("""
            SELECT ticket_id, title, status
            FROM ticketing.tickets
            ORDER BY ticket_id DESC
        """)
        
        if tickets:
            ticket_options = {f"#{t['ticket_id']} - {t['title']} (Current: {t['status']})": t['ticket_id'] for t in tickets}
            
            with st.form("update_status_form"):
                selected_ticket = st.selectbox(
                    "Select Ticket:",
                    options=list(ticket_options.keys())
                )
                new_status = st.selectbox("New Status:", ["open", "in_progress", "resolved"])
                
                submitted = st.form_submit_button("Update Status")
                
                if submitted:
                    try:
                        ticket_id = ticket_options[selected_ticket]
                        
                        lakebase.run_write("""
                            UPDATE ticketing.tickets
                            SET status = %s
                            WHERE ticket_id = %s
                        """, (new_status, ticket_id))
                        
                        st.success(f"✅ Ticket #{ticket_id} status updated to '{new_status}'!")
                        st.info("Refresh the page to see updated status.")
                        
                    except Exception as e:
                        st.error(f"Error updating status: {str(e)}")
        else:
            st.info("No tickets available to update.")
        
    except Exception as e:
        st.error(f"Error: {str(e)}")

# Tab 6: Statistics
with tab6:
    st.header("📊 Ticket Statistics")
    
    try:
        # Get overall statistics
        stats = lakebase.run_query("""
            SELECT 
                COUNT(*) as total_tickets,
                COUNT(*) FILTER (WHERE status = 'open') as open_tickets,
                COUNT(*) FILTER (WHERE status = 'in_progress') as in_progress_tickets,
                COUNT(*) FILTER (WHERE status = 'resolved') as resolved_tickets
            FROM ticketing.tickets
        """)
        
        if stats and len(stats) > 0:
            stat = stats[0]
            
            # Display key metrics
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("Total Tickets", stat['total_tickets'])
            with col2:
                st.metric("🔴 Open", stat['open_tickets'])
            with col3:
                st.metric("🟡 In Progress", stat['in_progress_tickets'])
            with col4:
                st.metric("🟢 Resolved", stat['resolved_tickets'])
            
            st.markdown("---")
            
            # Status distribution
            st.subheader("Status Distribution")
            status_data = {
                'Status': ['Open', 'In Progress', 'Resolved'],
                'Count': [
                    stat['open_tickets'],
                    stat['in_progress_tickets'],
                    stat['resolved_tickets']
                ]
            }
            st.bar_chart(status_data, x='Status', y='Count')
            
            st.markdown("---")
            
            # Most active users (by ticket creation)
            st.subheader("Most Active Users (Ticket Creators)")
            active_users = lakebase.run_query("""
                SELECT 
                    created_by,
                    COUNT(*) as ticket_count
                FROM ticketing.tickets
                GROUP BY created_by
                ORDER BY ticket_count DESC
                LIMIT 5
            """)
            
            if active_users:
                for user in active_users:
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.write(f"**{user['created_by']}**")
                    with col2:
                        st.write(f"{user['ticket_count']} tickets")
            else:
                st.info("No user data available.")
            
            st.markdown("---")
            
            # Message statistics
            st.subheader("Message Statistics")
            msg_stats = lakebase.run_query("""
                SELECT 
                    COUNT(*) as total_messages,
                    AVG(msg_count) as avg_messages_per_ticket
                FROM (
                    SELECT 
                        ticket_id,
                        COUNT(*) as msg_count
                    FROM ticketing.ticket_messages
                    GROUP BY ticket_id
                ) subq
            """)
            
            if msg_stats and len(msg_stats) > 0:
                msg_stat = msg_stats[0]
                col1, col2 = st.columns(2)
                
                with col1:
                    st.metric("Total Messages", msg_stat['total_messages'] or 0)
                with col2:
                    avg_msg = msg_stat['avg_messages_per_ticket']
                    if avg_msg:
                        st.metric("Avg Messages/Ticket", f"{float(avg_msg):.1f}")
                    else:
                        st.metric("Avg Messages/Ticket", "0.0")
            
            st.markdown("---")
            
            # Recent activity
            st.subheader("Recent Activity (Last 5 Tickets)")
            recent = lakebase.run_query("""
                SELECT 
                    ticket_id,
                    title,
                    status,
                    created_by,
                    created_at
                FROM ticketing.tickets
                ORDER BY created_at DESC
                LIMIT 5
            """)
            
            if recent:
                for ticket in recent:
                    status_icon = {
                        'open': '🔴',
                        'in_progress': '🟡',
                        'resolved': '🟢'
                    }.get(ticket['status'].lower(), '⚪')
                    
                    st.write(f"{status_icon} **#{ticket['ticket_id']}** - {ticket['title']}")
                    st.caption(f"Created by {ticket['created_by']} on {ticket['created_at']}")
            else:
                st.info("No recent activity.")
        else:
            st.info("No statistics available yet. Create some tickets to see stats!")
        
    except Exception as e:
        st.error(f"Error loading statistics: {str(e)}")

# Footer
st.markdown("---")
st.caption("⚡ ThunderHawk Ticketing System - Powered by Databricks + Lakebase")
